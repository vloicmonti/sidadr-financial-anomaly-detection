"""Module G — évaluation des règles métier sur une table canonique."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sidadr.business_rules.model import COMPARISONS, SET_OPS, Rule, RuleSet
from sidadr.config import Schema, default_schema
from sidadr.findings import Finding
from sidadr.profiling.profile import typed_frame

_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass(frozen=True)
class Violation:
    violation_id: str
    rule: Rule
    record_id: str
    field: str
    observed: str | None  # valeur d'origine
    compared_to: str | None = None  # valeur de l'autre champ, le cas échéant

    def explanation(self) -> str:
        shown = "(vide)" if self.observed is None else f"« {self.observed} »"
        text = f"{self.rule.description} : {self.field} = {shown}, attendu {self.rule.expected()}"
        if self.rule.other_field:
            text += f" (= {self.compared_to})"
        return text + "."

    def to_finding(self) -> Finding:
        return Finding(
            finding_id=f"RULE-{self.violation_id}",
            kind="rule_violation",
            severity=self.rule.severity,
            record_ids=(self.record_id,),
            rule_id=self.rule.rule_id,
            explanation=self.explanation(),
            recommendation=self.rule.recommendation,
            field=self.field,
            observed_value=self.observed,
            threshold=self.rule.expected(),
        )


@dataclass(frozen=True)
class RuleOutcome:
    rule: Rule
    applicable: int  # lignes concernées (après le filtre « when »), multipliées par les champs
    violations: int
    not_evaluable: int  # valeur absente ou illisible : la règle ne peut pas trancher


@dataclass
class RuleReport:
    n_rows: int
    outcomes: list[RuleOutcome]
    violations: list[Violation]
    ruleset: RuleSet
    notes: list[str] = field(default_factory=list)

    def for_rule(self, rule_id: str) -> list[Violation]:
        return [v for v in self.violations if v.rule.rule_id == rule_id]

    def rows_violating(self, dimension: str | None = "consistency") -> int:
        """Lignes distinctes violant au moins une règle de la dimension donnée."""
        return len(
            {
                v.record_id
                for v in self.violations
                if dimension is None or v.rule.dimension == dimension
            }
        )

    def findings(self) -> list[Finding]:
        return [v.to_finding() for v in self.violations]


def _matches_when(rule: Rule, typed: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=typed.index)
    for name, accepted in rule.when:
        mask &= typed[name].map(lambda v, acc=accepted: isinstance(v, str) and v.upper() in acc)
    return mask


def evaluate_rules(
    frame: pd.DataFrame, ruleset: RuleSet, schema: Schema | None = None
) -> RuleReport:
    schema = schema or default_schema()
    frame = frame.reset_index(drop=True)
    typed = typed_frame(frame, schema)
    record_ids = [str(r) for r in frame["record_id"]]

    outcomes: list[RuleOutcome] = []
    found: list[tuple[Rule, int, str, str | None, str | None]] = []
    for rule in ruleset.rules:
        rows = [int(i) for i in typed.index[_matches_when(rule, typed)]]
        violations = not_evaluable = 0
        for name in rule.fields:
            raw = frame[name].tolist()
            values = typed[name].tolist()
            others = typed[rule.other_field].tolist() if rule.other_field else None
            others_raw = frame[rule.other_field].tolist() if rule.other_field else None
            for i in rows:
                raw_value = raw[i]
                observed = (
                    None if raw_value is None or not str(raw_value).strip() else str(raw_value)
                )
                value = values[i]
                broken = False
                compared: str | None = None
                if rule.op == "not_null":
                    broken = observed is None
                elif value is None:
                    not_evaluable += 1
                    continue
                elif rule.op in SET_OPS:
                    inside = str(value) in rule.allowed
                    broken = not inside if rule.op == "in" else inside
                elif rule.op == "regex":
                    assert rule.pattern is not None
                    broken = rule.pattern.fullmatch(str(value)) is None
                elif rule.op in COMPARISONS:
                    target = rule.value
                    if others is not None and others_raw is not None:
                        target = others[i]
                        compared = None if others_raw[i] is None else str(others_raw[i])
                        if target is None:
                            not_evaluable += 1
                            continue
                    broken = not _COMPARE[rule.op](value, target)
                if broken:
                    violations += 1
                    found.append((rule, i, name, observed, compared))
        outcomes.append(RuleOutcome(rule, len(rows) * len(rule.fields), violations, not_evaluable))

    numbered = [
        Violation(f"{n:05d}", rule, record_ids[i], name, observed, compared)
        for n, (rule, i, name, observed, compared) in enumerate(found, start=1)
    ]
    return RuleReport(len(frame), outcomes, numbered, ruleset)
