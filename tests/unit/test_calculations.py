"""Contrôles de calcul entre colonnes (mode générique) : résultat = gauche opérateur droite."""

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from sidadr.analysis import analyze
from sidadr.business_rules import evaluate_rules, load_rules
from sidadr.business_rules.model import RuleSet, calculation_rule
from sidadr.cli_session import parse_check
from sidadr.config import ConfigError, FieldSpec, Schema
from sidadr.ingestion import MappingError

SCHEMA = Schema(
    (
        FieldSpec("ref", "Réf", "text", False, ()),
        FieldSpec("quantite", "Quantité", "decimal", False, ()),
        FieldSpec("prix", "Prix unitaire", "decimal", False, ()),
        FieldSpec("total", "Total", "decimal", False, ()),
    )
)


def _frame(rows: list[tuple[str | None, ...]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"record_id": f"f:{n}", "import_id": "f", "source_file": "f.csv", "source_row": n,
             "ref": r, "quantite": q, "prix": p, "total": t}
            for n, (r, q, p, t) in enumerate(rows, start=1)
        ],
        dtype=object,
    )  # fmt: skip


CHECK = {
    "result": "total",
    "left": "quantite",
    "operator": "*",
    "right": "prix",
    "tolerance": "0.01",
}


def test_calculation_finds_wrong_totals_and_skips_incomplete_rows() -> None:
    frame = _frame(
        [
            ("T1", "2", "2,00", "4,00"),  # juste
            ("T2", "3", "1,50", "9,00"),  # faux : 4,5 attendu
            ("T3", "3", "1,333", "4,00"),  # écart de 0,001 : toléré
            ("T4", None, "3,00", "6,00"),  # quantité absente : non évaluable
            ("T5", "UNKNOWN", "3,00", "6,00"),  # illisible : non évaluable
        ]
    )
    ruleset = RuleSet((calculation_rule(1, CHECK, SCHEMA),), {}, Path("test"))
    report = evaluate_rules(frame, ruleset, SCHEMA)
    assert [(v.record_id, v.compared_to) for v in report.violations] == [("f:2", "4.5")]
    assert report.outcomes[0].not_evaluable == 2
    explanation = report.violations[0].explanation()
    assert "Total doit valoir Quantité × Prix unitaire" in explanation
    assert "(± 0,01), calculé : 4,5." in explanation


@pytest.mark.parametrize(
    ("operator", "total", "broken"),
    [("*", "18", False), ("+", "9", False), ("-", "3", False), ("/", "2", False), ("/", "3", True)],
)
def test_every_operator(operator: str, total: str, broken: bool) -> None:
    check = {**CHECK, "operator": operator, "tolerance": "0"}
    frame = _frame([("T1", "6", "3", total)])  # 6 × 3, 6 + 3, 6 − 3, 6 ÷ 3
    report = evaluate_rules(
        frame, RuleSet((calculation_rule(1, check, SCHEMA),), {}, Path("t")), SCHEMA
    )
    assert bool(report.violations) is broken


def test_division_by_zero_is_not_evaluable() -> None:
    check = {**CHECK, "operator": "/"}
    frame = _frame([("T1", "2", "0", "1")])
    report = evaluate_rules(
        frame, RuleSet((calculation_rule(1, check, SCHEMA),), {}, Path("t")), SCHEMA
    )
    assert not report.violations and report.outcomes[0].not_evaluable == 1


def test_invalid_checks_are_refused_with_a_clear_message() -> None:
    with pytest.raises(ConfigError, match="n'est pas un champ numérique"):
        calculation_rule(1, {**CHECK, "left": "ref"}, SCHEMA)
    with pytest.raises(ConfigError, match="opérateur de calcul"):
        calculation_rule(1, {**CHECK, "operator": "%"}, SCHEMA)
    with pytest.raises(ConfigError, match="tolérance"):
        calculation_rule(1, {**CHECK, "tolerance": "-1"}, SCHEMA)


def test_formula_rules_can_be_written_in_yaml(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "rules:\n"
        "  - id: R-TOTAL\n"
        "    description: Total cohérent\n"
        "    severity: probable_inconsistency\n"
        "    check: {field: total, op: formula, left: quantite, operator: '*', right: prix, tolerance: 0.01}\n",
        encoding="utf-8",
    )
    rule = load_rules(path, SCHEMA)["R-TOTAL"]
    assert rule.formula == ("quantite", "*", "prix") and rule.tolerance == Decimal("0.01")
    assert rule.expected() == "= quantite × prix (± 0.01)"


def test_generic_analysis_scores_consistency_only_when_checks_exist() -> None:
    frame = _frame([("T1", "2", "2", "4"), ("T2", "3", "1.5", "9")])
    without = analyze(frame, SCHEMA, mode="generic")
    assert without.score["consistency"].evaluated is False
    with_checks = analyze(frame, SCHEMA, mode="generic", options={"checks": [CHECK]})
    consistency = with_checks.score["consistency"]
    assert consistency.evaluated is True and (consistency.numerator, consistency.denominator) == (
        1,
        2,
    )
    assert [f.rule_id for f in with_checks.findings()] == ["R-CALC-1"]


def test_cli_check_syntax() -> None:
    assert parse_check("Total = Quantité * Prix unitaire ~ 0.01") == {
        "result": "Total", "left": "Quantité", "operator": "*", "right": "Prix unitaire", "tolerance": "0.01",
    }  # fmt: skip
    minus = parse_check("Marge = Prix-HT - Coût")
    assert (minus["left"], minus["operator"], minus["right"]) == ("Prix-HT", "-", "Coût")
    with pytest.raises(MappingError, match="opérateur introuvable"):
        parse_check("Total = Quantité x Prix")
    with pytest.raises(MappingError, match="RÉSULTAT"):
        parse_check("Quantité * Prix")
