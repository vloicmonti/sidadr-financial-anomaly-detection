"""Analyse complète d'une table : profil, score et constats de tous les détecteurs.

Les identifiants de constats (DUP-00012…) dépendent de l'ordre des résultats et peuvent
changer d'une analyse à l'autre. Les décisions humaines sont donc rattachées à une **clé
stable** : le type de constat, la règle et les enregistrements concernés.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sidadr.anomaly_detection import AnomalyReport, detect_anomalies
from sidadr.business_rules import RuleReport, evaluate_rules, load_rules
from sidadr.config import (
    Schema,
    Settings,
    default_schema,
    default_settings,
    load_anomaly_settings,
    load_cross_entity_settings,
    load_duplicate_settings,
    load_matching_settings,
)
from sidadr.cross_entity import EntityReport, group_entities
from sidadr.deduplication import DuplicateKind, DuplicateReport, detect_duplicates
from sidadr.findings import Finding
from sidadr.name_matching import MatchReport, match_names
from sidadr.profiling import DatasetProfile, QualityScore, compute_score, profile_frame

#: Niveaux de doublons comptés dans l'unicité (les combinaisons sont des alertes).
KEY_LEVELS = (DuplicateKind.EXACT_ROW, DuplicateKind.KEY, DuplicateKind.CROSS_FILE)


def finding_key(finding: Finding) -> str:
    """Clé stable d'un constat : indépendante de l'ordre et de la numérotation."""
    text = "|".join(
        [finding.kind, finding.rule_id, finding.field or "", *sorted(finding.record_ids)]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class AnalysisResult:
    profile: DatasetProfile
    score: QualityScore
    duplicates: DuplicateReport
    names: MatchReport
    entities: EntityReport
    anomalies: AnomalyReport
    rules: RuleReport

    def findings(self) -> list[Finding]:
        return [
            *self.rules.findings(),
            *self.duplicates.findings(),
            *self.anomalies.findings(),
            *self.names.findings(),
            *self.entities.findings(),
        ]


def analyze(
    frame: pd.DataFrame,
    schema: Schema | None = None,
    settings: Settings | None = None,
    rules_path: Path | None = None,
    rule_overrides: dict[str, Any] | None = None,
) -> AnalysisResult:
    schema = schema or default_schema()
    settings = settings or default_settings()
    profile = profile_frame(frame, schema, settings)
    duplicates = detect_duplicates(frame, load_duplicate_settings(schema=schema), schema)
    names = match_names(frame, load_matching_settings(), schema)
    entities = group_entities(names, load_cross_entity_settings())
    anomalies = detect_anomalies(frame, load_anomaly_settings(schema=schema), schema)
    rules = evaluate_rules(frame, load_rules(rules_path, schema, rule_overrides), schema)
    score = compute_score(
        profile,
        duplicate_rows=duplicates.rows_involved(KEY_LEVELS),
        rule_violation_rows=rules.rows_violating("consistency"),
    )
    return AnalysisResult(profile, score, duplicates, names, entities, anomalies, rules)


def findings_to_records(findings: list[Finding], decisions: dict[str, str]) -> list[dict[str, Any]]:
    """Constats sérialisables, avec leur clé stable et leur statut (décision active)."""
    records = []
    for f in findings:
        key = finding_key(f)
        record = dataclasses.asdict(f)
        record["severity"] = f.severity.value
        record["record_ids"] = list(f.record_ids)
        record["key"] = key
        record["status"] = decisions.get(key, "open")
        records.append(record)
    return records


def save_findings(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )


def load_findings(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return data
