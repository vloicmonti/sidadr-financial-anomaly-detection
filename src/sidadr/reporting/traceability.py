"""Fiche de traçabilité commune aux exports et au rapport.

Elle répond à la question : « d'où viennent ces chiffres et comment les reproduire ? »
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sidadr import __version__
from sidadr.config import config_dir
from sidadr.correction import CorrectionService

CONFIG_FILES = (
    "schema.yaml",
    "settings.yaml",
    "quality_score.yaml",
    "duplicates.yaml",
    "matching.yaml",
    "cross_entity.yaml",
    "anomalies.yaml",
    "rules/transactions.yaml",
)

LIMITS = (
    "Prototype local mono-utilisateur : l'auteur des actions est déclaré, pas authentifié.",
    "Les seuils ont été calibrés sur des données synthétiques ; leurs performances sur des données réelles ne sont pas garanties.",
    "Une similarité de nom ne prouve pas une identité ; une valeur atypique n'est ni une erreur certaine ni une fraude.",
    "Le score qualité est un indicateur interne paramétrable, pas une norme.",
)


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_traceability(
    service: CorrectionService, summary: dict[str, Any] | None
) -> dict[str, Any]:
    store = service.store
    journal = service.history()
    check = service.verify_journal()
    if summary is None:
        analysis_stale = True
    elif "data_changes_at_analysis" in summary:
        analysis_stale = summary["data_changes_at_analysis"] != service.data_changes()
    else:  # résumé antérieur : il comptait toutes les entrées du journal
        analysis_stale = summary.get("journal_entries_at_analysis") != len(journal)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sidadr_version": __version__,
        "session_id": store.session_id,
        "session_name": store.metadata.get("name"),
        "session_created_at": store.metadata.get("created_at"),
        "sources": store.metadata.get("imports", []),
        "original_rows": store.metadata.get("rows"),
        "original_sha256": store.metadata.get("original_sha256"),
        "working_rows": len(service.working_frame()),
        "excluded_records": len(service.excluded_records()),
        "active_corrections": len(service.active_corrections()),
        "active_decisions": len(service.decisions()),
        "journal_entries": len(journal),
        "journal_integrity": "intact"
        if check.valid
        else f"altéré à l'entrée #{check.first_broken_entry}",
        "analysis_generated_at": summary.get("generated_at") if summary else None,
        "analysis_up_to_date": not analysis_stale,
        "rule_parameters": summary.get("rule_parameters", {}) if summary else {},
        "configuration_sha256": {name: file_sha256(config_dir() / name) for name in CONFIG_FILES},
        "limits": list(LIMITS),
    }


def traceability_rows(trace: dict[str, Any]) -> list[tuple[str, str]]:
    """Version « clé / valeur » à plat, pour un onglet Excel ou un tableau HTML."""
    rows: list[tuple[str, str]] = []
    for key, value in trace.items():
        if key == "sources":
            for source in value:
                rows.append(
                    (
                        f"source : {source['source_file']}",
                        f"{source['rows']} lignes importées, {source['rows_rejected']} rejetées (import {source['import_id']})",
                    )
                )
        elif key == "configuration_sha256":
            for name, digest in value.items():
                rows.append((f"configuration : {name}", digest or "absent"))
        elif key == "rule_parameters":
            for name, param in value.items():
                rows.append((f"paramètre de règles : {name}", str(param)))
        elif key == "limits":
            for i, limit in enumerate(value, start=1):
                rows.append((f"limite {i}", limit))
        else:
            rows.append((key, "" if value is None else str(value)))
    return rows
