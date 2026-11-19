from pathlib import Path

import pandas as pd
import pytest

from sidadr.config import (
    CombinationRule,
    ConfigError,
    DuplicateSettings,
    Schema,
    default_schema,
    load_duplicate_settings,
)
from sidadr.deduplication import DuplicateKind, detect_duplicates
from sidadr.findings import Severity
from sidadr.profiling import compute_score, profile_frame

SAME_OPERATION = CombinationRule(
    "same_operation",
    "",
    ("account_number", "transaction_date", "operation_type", "amount", "currency"),
)
SETTINGS = DuplicateSettings(("transaction_id",), False, (SAME_OPERATION,))


@pytest.fixture(scope="module")
def schema() -> Schema:
    return default_schema()


def _row(n: int, import_id: str = "a", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "record_id": f"{import_id}:{n}", "import_id": import_id, "source_file": f"{import_id}.csv",
        "source_row": n, "transaction_id": f"TX-{n}", "bank_code": "BALP", "branch_code": "BALP-001",
        "branch_city": "Douala", "customer_id": f"C-{n}", "customer_name": f"CLIENT {n}",
        "birth_date": None, "phone": None, "account_number": f"A-{n}", "operation_type": "DEPOSIT",
        "transaction_date": "2025-04-01", "value_date": "2025-04-01", "amount": str(1000 + n),
        "currency": "XAF",
    }  # fmt: skip
    row.update(overrides)
    return row


def _copy(
    row: dict[str, object], n: int, import_id: str | None = None, **overrides: object
) -> dict[str, object]:
    import_id = import_id or str(row["import_id"])
    return {**row, "record_id": f"{import_id}:{n}", "import_id": import_id,
            "source_file": f"{import_id}.csv", "source_row": n, **overrides}  # fmt: skip


def _detect(rows: list[dict[str, object]], schema: Schema, settings: DuplicateSettings = SETTINGS):  # type: ignore[no-untyped-def]
    return detect_duplicates(pd.DataFrame(rows, dtype=object), settings, schema)


def test_clean_data_has_no_duplicates(schema: Schema) -> None:
    report = _detect([_row(n) for n in range(5)], schema)
    assert report.groups == []
    assert report.rows_involved() == 0


def test_identical_rows_in_one_file_are_certain_errors(schema: Schema) -> None:
    base = _row(1)
    report = _detect([base, _row(2), _copy(base, 3)], schema)
    (group,) = report.groups
    assert group.kind is DuplicateKind.EXACT_ROW
    assert group.severity is Severity.CERTAIN_ERROR
    assert group.record_ids == ("a:1", "a:3")
    assert group.differences == ()
    assert group.sources == ("a.csv:1", "a.csv:3")


def test_same_key_with_different_values_lists_the_differences(schema: Schema) -> None:
    base = _row(1)
    report = _detect([base, _copy(base, 2, amount="1 050")], schema)
    (group,) = report.groups
    assert group.kind is DuplicateKind.KEY
    (diff,) = group.differences
    assert diff.field == "amount"
    assert diff.values == (("a:1", "1001"), ("a:2", "1 050"))
    assert "amount" in group.explanation()


def test_same_key_in_two_files_is_a_cross_file_duplicate_despite_formats(schema: Schema) -> None:
    base = _row(1)
    other = _copy(
        base,
        7,
        import_id="b",
        transaction_date="01/04/2025",
        value_date="01/04/2025",
        amount="1 001,00",
    )
    filler = _row(8, "b", transaction_date="25/04/2025", value_date="25/04/2025")
    report = _detect([base, _row(2), other, filler], schema)
    (group,) = report.groups
    assert group.kind is DuplicateKind.CROSS_FILE
    assert group.severity is Severity.PROBABLE_INCONSISTENCY
    assert group.imports == ("a", "b")
    assert {"transaction_date", "amount"} <= set(group.identical_fields)
    assert group.differences == ()


def test_key_comparison_case_sensitivity_is_configurable(schema: Schema) -> None:
    rows = [_row(1, transaction_id="tx-1"), _row(2, transaction_id="TX-1")]
    assert len(_detect(rows, schema).groups) == 1
    strict = DuplicateSettings(("transaction_id",), True, ())
    assert _detect(rows, schema, strict).groups == []


def test_rows_without_key_are_compared_on_the_full_row(schema: Schema) -> None:
    base = _row(1, transaction_id=None)
    report = _detect([base, _copy(base, 2), _row(3, transaction_id=None)], schema)
    (group,) = report.groups
    assert group.kind is DuplicateKind.EXACT_ROW and group.rule == "ligne complète"
    assert report.rows_without_key == 3
    assert report.notes


def test_combination_flags_same_operation_without_common_id(schema: Schema) -> None:
    a = _row(1, account_number="A-X", amount="5000")
    b = _row(2, account_number="A-X", amount="5000")
    report = _detect([a, b, _row(3)], schema)
    (group,) = report.groups
    assert group.kind is DuplicateKind.COMBINATION
    assert group.severity is Severity.REVIEW_ALERT
    assert group.rule == "same_operation"
    assert "transaction_id" in {d.field for d in group.differences}


def test_combination_already_explained_by_the_key_is_not_reported_twice(schema: Schema) -> None:
    base = _row(1)
    report = _detect([base, _copy(base, 2)], schema)
    assert [g.kind for g in report.groups] == [DuplicateKind.EXACT_ROW]


def test_groups_can_have_more_than_two_records(schema: Schema) -> None:
    base = _row(1)
    report = _detect([base, _copy(base, 2), _copy(base, 3)], schema)
    (group,) = report.groups
    assert group.size == 3 and report.rows_involved() == 3


def test_detection_never_modifies_the_data(schema: Schema) -> None:
    base = _row(1)
    frame = pd.DataFrame([base, _copy(base, 2, amount="9")], dtype=object)
    before = frame.copy()
    detect_duplicates(frame, SETTINGS, schema)
    pd.testing.assert_frame_equal(frame, before)


def test_groups_become_findings(schema: Schema) -> None:
    base = _row(1)
    (finding,) = _detect([base, _copy(base, 2, amount="9")], schema).findings()
    assert finding.kind == "duplicate_key"
    assert finding.severity is Severity.CERTAIN_ERROR
    assert finding.record_ids == ("a:1", "a:2")
    assert finding.rule_id == "DUP:transaction_id"
    assert finding.recommendation


def test_uniqueness_uses_the_duplicate_detector_when_given(schema: Schema) -> None:
    base = _row(1)
    rows = [base, _copy(base, 2, amount="9"), _row(3), _row(4)]
    profile = profile_frame(pd.DataFrame(rows, dtype=object), schema)
    report = _detect(rows, schema)
    assert compute_score(profile)["uniqueness"].numerator == 0  # lignes complètes différentes
    score = compute_score(profile, duplicate_rows=report.rows_involved())
    assert score["uniqueness"].value == pytest.approx(1 - 2 / 4)


def test_default_configuration_loads(schema: Schema) -> None:
    settings = load_duplicate_settings(schema=schema)
    assert settings.key_fields == ("transaction_id",)
    assert settings.combinations and settings.combinations[0].name == "same_operation"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("business_key:\n  fields: []\n", "au moins un champ"),
        ("business_key:\n  fields: [inconnu]\n", "inconnus"),
        (
            "business_key:\n  fields: [transaction_id]\ncombinations:\n  - name: x\n    fields: [amount]\n",
            "2 champs",
        ),
    ],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, schema: Schema, content: str, message: str
) -> None:
    (tmp_path / "duplicates.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_duplicate_settings(tmp_path, schema)
