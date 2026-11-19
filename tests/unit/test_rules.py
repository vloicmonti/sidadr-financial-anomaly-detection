from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from sidadr.business_rules import RuleReport, evaluate_rules, load_rules
from sidadr.config import ConfigError
from sidadr.findings import Severity


def _row(n: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "record_id": f"a:{n}", "import_id": "a", "source_file": "a.csv", "source_row": n,
        "transaction_id": f"TX-{n}", "bank_code": "BALP", "branch_code": "BALP-001",
        "branch_city": "Douala", "customer_id": f"C-{n}", "customer_name": "CLIENT", "birth_date": None,
        "phone": None, "account_number": f"A-{n}", "operation_type": "DEPOSIT",
        "transaction_date": "2025-04-01", "value_date": "2025-04-02", "amount": "1000", "currency": "XAF",
    }  # fmt: skip
    row.update(overrides)
    return row


def _rules(tmp_path: Path, body: str, header: str = "") -> Path:
    (tmp_path / "branches.csv").write_text("branch_code\nBALP-001\nBALP-002\n", encoding="utf-8")
    path = tmp_path / "rules.yaml"
    path.write_text(
        "parameters:\n  reference_date: 2025-06-30\n  cap: 500\n"
        "references:\n  branches: {file: branches.csv, column: branch_code}\n"
        f"{header}rules:\n{body}",
        encoding="utf-8",
    )
    return path


def _run(tmp_path: Path, body: str, rows: list[dict[str, object]], **load: object) -> RuleReport:
    ruleset = load_rules(_rules(tmp_path, body), **load)  # type: ignore[arg-type]
    return evaluate_rules(pd.DataFrame(rows, dtype=object), ruleset)


def _ids(report: RuleReport) -> list[str]:
    return [v.record_id for v in report.violations]


RULE = "  - id: R1\n    description: test\n    severity: probable_inconsistency\n"


def test_comparison_with_a_fixed_value_and_a_when_filter(tmp_path: Path) -> None:
    body = (
        RULE
        + "    when: {operation_type: [deposit]}\n    check: {field: amount, op: '>=', value: 0}\n"
    )
    rows = [_row(1, amount="-5"), _row(2, amount="-5", operation_type="WITHDRAWAL"), _row(3)]
    report = _run(tmp_path, body, rows)
    assert _ids(report) == ["a:1"]
    assert report.outcomes[0].applicable == 2  # le retrait n'est pas concerné


def test_parameters_are_substituted(tmp_path: Path) -> None:
    body = RULE + "    check: {field: transaction_date, op: '<=', value: $reference_date}\n"
    rows = [_row(1, transaction_date="2025-07-01"), _row(2, transaction_date="2025-06-30")]
    assert _ids(_run(tmp_path, body, rows)) == ["a:1"]
    later = _run(tmp_path, body, rows, overrides={"reference_date": "2025-12-31"})
    assert later.violations == []


def test_comparison_between_two_fields(tmp_path: Path) -> None:
    body = RULE + "    check: {field: value_date, op: '>=', other_field: transaction_date}\n"
    rows = [_row(1, value_date="2025-03-01"), _row(2), _row(3, transaction_date=None)]
    report = _run(tmp_path, body, rows)
    assert _ids(report) == ["a:1"]
    assert report.outcomes[0].not_evaluable == 1
    assert report.violations[0].compared_to == "2025-04-01"


def test_dates_follow_each_file_convention(tmp_path: Path) -> None:
    body = RULE + "    check: {field: transaction_date, op: '<=', value: $reference_date}\n"
    rows = [_row(1, transaction_date="01/07/2025"), _row(2, transaction_date="25/06/2025")]
    assert _ids(_run(tmp_path, body, rows)) == ["a:1"]


def test_reference_and_inline_lists(tmp_path: Path) -> None:
    body = (
        RULE + "    check: {field: branch_code, op: in, reference: branches}\n"
        "  - id: R2\n    description: devise\n    severity: certain_error\n"
        "    check: {field: currency, op: not_in, values: [XXX]}\n"
    )
    rows = [_row(1, branch_code="BALP-099"), _row(2, currency="XXX"), _row(3)]
    report = _run(tmp_path, body, rows)
    assert [(v.rule.rule_id, v.record_id) for v in report.violations] == [
        ("R1", "a:1"),
        ("R2", "a:2"),
    ]
    assert "référentiel « branches »" in report.violations[0].explanation()


def test_not_null_over_several_fields_counts_for_completeness_only(tmp_path: Path) -> None:
    body = (
        "  - id: R-MAND\n    description: obligatoires\n    severity: certain_error\n    dimension: completeness\n"
        "    check: {fields: [customer_name, amount], op: not_null}\n"
    )
    rows = [_row(1, customer_name=None, amount="  "), _row(2)]
    report = _run(tmp_path, body, rows)
    assert [(v.record_id, v.field) for v in report.violations] == [
        ("a:1", "customer_name"),
        ("a:1", "amount"),
    ]
    assert report.rows_violating("consistency") == 0
    assert report.rows_violating("completeness") == 1


def test_regex_and_unreadable_values(tmp_path: Path) -> None:
    body = RULE + "    check: {field: transaction_id, op: regex, pattern: 'TX-[0-9]+'}\n"
    rows = [
        _row(1, transaction_id="TX-12"),
        _row(2, transaction_id="tx 12"),
        _row(3, transaction_id=None),
    ]
    report = _run(tmp_path, body, rows)
    assert _ids(report) == ["a:2"]
    assert report.outcomes[0].not_evaluable == 1


def test_values_are_never_executed_as_code(tmp_path: Path) -> None:
    body = (
        RULE + "    check: {field: customer_name, op: '==', value: \"__import__('os').getcwd()\"}\n"
    )
    report = _run(tmp_path, body, [_row(1)])
    assert report.violations[0].rule.value == "__import__('os').getcwd()"


def test_violations_become_findings(tmp_path: Path) -> None:
    body = (
        RULE + "    recommendation: vérifier\n    check: {field: amount, op: '<=', value: $cap}\n"
    )
    report = _run(tmp_path, body, [_row(1, amount="600")])
    finding = report.violations[0].to_finding()
    assert finding.severity is Severity.PROBABLE_INCONSISTENCY
    assert finding.rule_id == "R1" and finding.observed_value == "600"
    assert finding.threshold == "<= 500" and finding.recommendation == "vérifier"


def test_default_rules_load_with_typed_values() -> None:
    ruleset = load_rules()
    assert ruleset["R-DATE-FUTURE"].value == date(2025, 6, 30)
    assert ruleset["R-FEE-CAP"].value == Decimal(50000)
    assert len(ruleset["R-BRANCH-REF"].allowed) > 0


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (RULE + "    check: {field: inconnu, op: not_null}\n", "inconnus"),
        (RULE + "    check: {field: amount, op: '~='}\n", "opérateur"),
        ("  - id: R1\n    severity: grave\n    check: {field: amount, op: not_null}\n", "sévérité"),
        (
            RULE
            + "    check: {field: amount, op: not_null}\n"
            + RULE
            + "    check: {field: amount, op: not_null}\n",
            "double",
        ),
        (RULE + "    check: {field: amount, op: '<', value: $absent}\n", "non défini"),
        (RULE + "    check: {field: branch_code, op: in, reference: absent}\n", "non déclaré"),
        (RULE + "    check: {field: transaction_id, op: regex, pattern: '(['}\n", "régulière"),
        (RULE + "    check: {field: customer_name, op: '<', value: A}\n", "texte"),
        (
            RULE + "    check: {field: amount, op: '>', other_field: transaction_date}\n",
            "même type",
        ),
        (RULE + "    check: {field: transaction_date, op: '<', value: hier}\n", "AAAA-MM-JJ"),
        (RULE + "    check: {field: amount, op: '<'}\n", "obligatoire"),
    ],
)
def test_invalid_rules_are_rejected_at_load_time(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_rules(_rules(tmp_path, body))


def test_missing_reference_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "references:\n  b: {file: absent.csv, column: x}\nrules:\n"
        + RULE
        + "    check: {field: amount, op: not_null}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="introuvable"):
        load_rules(path)
