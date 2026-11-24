"""Analyse complète d'une table : profil, score et constats de tous les détecteurs.

Les identifiants de constats (DUP-00012…) dépendent de l'ordre des résultats et peuvent
changer d'une analyse à l'autre. Les décisions humaines sont donc rattachées à une **clé
stable** : le type de constat, la règle et les enregistrements concernés.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sidadr.anomaly_detection import AnomalyReport, detect_anomalies
from sidadr.business_rules import Rule, RuleReport, RuleSet, evaluate_rules, load_rules
from sidadr.business_rules.model import TYPE_EXPECTATIONS
from sidadr.config import (
    AnomalySettings,
    DuplicateSettings,
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
from sidadr.findings import Finding, Severity
from sidadr.ingestion.values import missing_marker_set, placeholder
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
    names: MatchReport | None  # mode bancaire uniquement
    entities: EntityReport | None  # mode bancaire uniquement
    anomalies: AnomalyReport
    rules: RuleReport
    mode: str = "banking"

    def findings(self) -> list[Finding]:
        return [
            *self.rules.findings(),
            *self.duplicates.findings(),
            *self.anomalies.findings(),
            *(self.names.findings() if self.names else []),
            *(self.entities.findings() if self.entities else []),
        ]


def _generic_rules(schema: Schema, markers: frozenset[str] = frozenset()) -> RuleSet:
    """Règles par défaut du mode générique.

    Champs désignés obligatoires renseignés (une valeur de remplissage comme « UNKNOWN »
    vaut absence), et toute valeur renseignée lisible dans le type de sa colonne.
    """
    rules: list[Rule] = []
    required = tuple(f.name for f in schema.fields if f.required)
    if required:
        rules.append(
            Rule(
                rule_id="R-MANDATORY",
                description="Les champs désignés obligatoires doivent être renseignés",
                fields=required,
                op="not_null",
                severity=Severity.CERTAIN_ERROR,
                recommendation="Compléter la valeur depuis la source ; ne pas l'inventer.",
                dimension="completeness",
            )
        )
    for rule_id, ftype, label in (("R-NUMBER", "decimal", "nombre"), ("R-DATE", "date", "date")):
        fields = tuple(f.name for f in schema.fields if f.type == ftype)
        if fields:
            rules.append(
                Rule(
                    rule_id=rule_id,
                    description=f"Les valeurs des colonnes de type {label} doivent être lisibles",
                    fields=fields,
                    op="valid_type",
                    severity=Severity.CERTAIN_ERROR,
                    recommendation="Corriger la valeur depuis la source, ou revoir le type "
                    "de la colonne si la détection s'est trompée.",
                    dimension="validity",
                    expected_type=TYPE_EXPECTATIONS[ftype],
                )
            )
    return RuleSet(
        tuple(rules), {}, Path("(règles par défaut du mode générique)"), missing_markers=markers
    )


def _without_placeholders(
    frame: pd.DataFrame, schema: Schema, markers: frozenset[str]
) -> pd.DataFrame:
    """Vue d'analyse où les valeurs de remplissage sont vides (les données ne changent pas)."""
    if not markers:
        return frame
    view = frame.copy()
    for name in schema.names:
        view[name] = [None if placeholder(v, markers) else v for v in view[name].tolist()]
    return view


def analyze(
    frame: pd.DataFrame,
    schema: Schema | None = None,
    settings: Settings | None = None,
    rules_path: Path | None = None,
    rule_overrides: dict[str, Any] | None = None,
    mode: str = "banking",
    options: dict[str, Any] | None = None,
) -> AnalysisResult:
    """Analyse complète. En mode générique, les détecteurs propres aux opérations bancaires
    (noms de clients, multi-banques, règles bancaires) ne sont pas lancés."""
    schema = schema or default_schema()
    settings = settings or default_settings()
    options = options or {}
    # Valeurs de remplissage : mode générique seulement (le mode bancaire a ses référentiels).
    markers = (
        missing_marker_set(settings.profiling.missing_markers) if mode == "generic" else frozenset()
    )
    profile = profile_frame(frame, schema, settings, markers)

    if mode == "generic":
        view = _without_placeholders(frame, schema, markers)
        key_fields = tuple(str(f) for f in options.get("key_fields", []))
        duplicates = detect_duplicates(view, DuplicateSettings(key_fields, False, ()), schema)
        numeric = tuple(f.name for f in schema.fields if f.type == "decimal")
        group_by = tuple(str(f) for f in options.get("group_by", []))
        anomalies = detect_anomalies(
            view, dataclasses.replace(AnomalySettings(), fields=numeric, group_by=group_by), schema
        )
        user_rules = rules_path or (
            Path(options["rules_path"]) if options.get("rules_path") else None
        )
        ruleset = (
            dataclasses.replace(
                load_rules(user_rules, schema, rule_overrides), missing_markers=markers
            )
            if user_rules
            else _generic_rules(schema, markers)
        )
        rules = evaluate_rules(frame, ruleset, schema)
        score = compute_score(
            profile,
            duplicate_rows=duplicates.rows_involved(KEY_LEVELS),
            rule_violation_rows=rules.rows_violating("consistency") if user_rules else None,
        )
        return AnalysisResult(profile, score, duplicates, None, None, anomalies, rules, mode)

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
    return AnalysisResult(profile, score, duplicates, names, entities, anomalies, rules, mode)


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


MAX_TOP_VALUES = 5
MAX_INVALID_SAMPLES = 20


def profile_to_dict(profile: DatasetProfile) -> dict[str, Any]:
    """Profil par colonne, sérialisable : ce que l'écran « Qualité des données » affiche."""
    columns = []
    for c in profile.columns:
        stats = c.numeric
        columns.append(
            {
                "name": c.name,
                "label": c.label,
                "type": c.expected_type,
                "required": c.required,
                "rows": c.n_rows,
                "missing": c.missing,
                "distinct": c.distinct,
                "invalid": c.invalid,
                "checked_for_validity": c.checked_for_validity,
                "invalid_reasons": dict(c.invalid_reasons),
                "invalid_samples": c.invalid_record_ids[:MAX_INVALID_SAMPLES],
                "placeholders": dict(c.placeholders),
                "decisions": list(c.decisions),
                "numeric": None
                if stats is None
                else {
                    "count": stats.count,
                    "min": str(stats.minimum),
                    "q1": str(stats.q1),
                    "median": str(stats.median),
                    "q3": str(stats.q3),
                    "max": str(stats.maximum),
                    "mean": round(stats.mean, 4),
                    "std": round(stats.std, 4),
                },
                "date_range": None
                if c.date_range is None
                else [c.date_range[0].isoformat(), c.date_range[1].isoformat()],
                "top_values": [[v, n] for v, n in c.top_values[:MAX_TOP_VALUES]],
            }
        )
    return {
        "rows": profile.n_rows,
        "duplicate_rows": profile.duplicate_rows,
        "duplicate_groups": profile.duplicate_groups,
        "columns": columns,
    }


def summarize(
    result: AnalysisResult, records: list[dict[str, Any]], data_changes: int
) -> dict[str, Any]:
    """Résumé persistant d'une analyse, réutilisé par les exports et le rapport."""
    from datetime import UTC, datetime

    from sidadr import __version__

    def by(key: str) -> dict[str, int]:
        return dict(sorted(Counter(str(r[key]) for r in records).items()))

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sidadr_version": __version__,
        "mode": result.mode,
        "rows_analyzed": result.profile.n_rows,
        # Seules les modifications de données périment l'analyse ; une décision n'en est pas une.
        "data_changes_at_analysis": data_changes,
        "score": dataclasses.asdict(result.score),
        "profile": profile_to_dict(result.profile),
        "findings": {
            "total": len(records),
            "by_severity": by("severity"),
            "by_kind": by("kind"),
            "by_status": by("status"),
        },
        "rules": [
            {
                "rule_id": o.rule.rule_id,
                "description": o.rule.description,
                "severity": o.rule.severity.value,
                "dimension": o.rule.dimension,
                "checked": o.applicable,
                "violations": o.violations,
                "not_evaluable": o.not_evaluable,
            }
            for o in result.rules.outcomes
        ],
        "rule_parameters": {k: str(v) for k, v in result.rules.ruleset.parameters.items()},
        "anomaly_groups_skipped": len(result.anomalies.skipped_groups),
    }


def save_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def load_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
