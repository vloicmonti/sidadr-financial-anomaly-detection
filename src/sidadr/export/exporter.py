"""Module I — exports CSV et XLSX d'une session.

Contenu exporté : la vue de travail (données corrigées, sans les enregistrements exclus),
les constats avec leur statut, le journal des corrections et une fiche de traçabilité
(onglet « Traçabilité » en XLSX, fichier `.meta.json` en CSV).

Protection contre l'injection de formules (CSV) : une cellule texte commençant par `=`,
`+`, `-`, `@`, une tabulation ou un retour chariot est préfixée d'une apostrophe, sauf
s'il s'agit d'un nombre (un montant négatif reste un montant). En XLSX, toutes les cellules
sont écrites explicitement comme du texte : aucune ne peut être interprétée comme formule.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from sidadr.analysis import load_findings, load_summary
from sidadr.correction import CorrectionService
from sidadr.ingestion.values import parse_decimal
from sidadr.reporting import build_traceability, traceability_rows

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
TECHNICAL = ("record_id", "import_id", "source_file", "source_row")


@dataclass
class ExportResult:
    files: list[Path]
    neutralized_cells: int
    traceability: dict[str, Any]


class _Neutralizer:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value)
        if text.startswith(DANGEROUS_PREFIXES) and parse_decimal(text) is None:
            self.count += 1
            return "'" + text
        return text


def _tables(service: CorrectionService) -> dict[str, tuple[list[str], list[list[object]]]]:
    working = service.working_frame()
    columns = [*TECHNICAL, *service.schema.names]
    data_rows = (
        working[columns].astype(object).where(working[columns].notna(), None).values.tolist()
    )

    findings = load_findings(service.store.findings_path)
    decisions = service.decisions()
    finding_header = ["finding_id", "kind", "severity", "status", "rule_id", "field", "observed_value",
                      "threshold", "score", "record_ids", "explanation", "recommendation"]  # fmt: skip
    finding_rows: list[list[object]] = [
        [f["finding_id"], f["kind"], f["severity"], decisions.get(f["key"], "open"), f["rule_id"],
         f.get("field"), f.get("observed_value"), f.get("threshold"), f.get("score"),
         " ".join(f["record_ids"]), f["explanation"], f["recommendation"]]
        for f in findings
    ]  # fmt: skip

    journal_header = ["entry_id", "ts_utc", "actor", "action_type", "target", "record_id", "field",
                      "old_value", "new_value", "reason", "finding_id"]  # fmt: skip
    journal_rows: list[list[object]] = [
        [e.entry_id, e.ts_utc, e.actor, e.action_type, e.target, e.record_id, e.field,
         e.old_value, e.new_value, e.reason, e.finding_id]
        for e in service.history()
    ]  # fmt: skip
    return {
        "donnees": (columns, data_rows),
        "constats": (finding_header, finding_rows),
        "journal": (journal_header, journal_rows),
    }


def export_session(service: CorrectionService, out_dir: Path, fmt: str = "xlsx") -> ExportResult:
    if fmt not in ("xlsx", "csv"):
        raise ValueError("Format attendu : xlsx ou csv.")
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = build_traceability(service, load_summary(service.store.summary_path))
    tables = _tables(service)
    stem = f"sidadr_{service.store.session_id}"
    neutralize = _Neutralizer()
    files: list[Path] = []

    if fmt == "csv":
        for name, (header, rows) in tables.items():
            path = out_dir / f"{stem}_{name}.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(header)
                writer.writerows([[neutralize(v) for v in row] for row in rows])
            files.append(path)
        trace["neutralized_cells"] = neutralize.count
        meta = out_dir / f"{stem}.meta.json"
        meta.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append(meta)
        return ExportResult(files, neutralize.count, trace)

    workbook = Workbook()
    sheet_names = {"donnees": "Données", "constats": "Constats", "journal": "Journal"}
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E8C")
    first = True
    for name, (header, rows) in tables.items():
        ws = workbook.active if first else workbook.create_sheet()
        first = False
        ws.title = sheet_names[name]
        ws.append(header)
        for cell in ws[1]:
            cell.font, cell.fill = header_font, header_fill
        for row in rows:
            ws.append([None if v is None else str(v) for v in row])
        for row_cells in ws.iter_rows(min_row=2):
            for cell in row_cells:
                if cell.value is not None:
                    cell.data_type = "s"  # texte explicite : jamais une formule
        ws.freeze_panes = "A2"
        for index, title in enumerate(header, start=1):
            ws.column_dimensions[get_column_letter(index)].width = min(48, max(12, len(title) + 4))
    trace["neutralized_cells"] = 0
    ws = workbook.create_sheet("Traçabilité")
    ws.append(["Élément", "Valeur"])
    for cell in ws[1]:
        cell.font, cell.fill = header_font, header_fill
    for key, value in traceability_rows(trace):
        ws.append([key, value])
        for cell in ws[ws.max_row]:
            cell.data_type = "s"
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 42, 90
    path = out_dir / f"{stem}.xlsx"
    workbook.save(path)
    files.append(path)
    return ExportResult(files, 0, trace)


def read_back(path: Path) -> pd.DataFrame:
    """Relit les données exportées (pour vérifier qu'un export restitue les mêmes valeurs)."""
    if path.suffix == ".xlsx":
        frame = pd.read_excel(path, sheet_name="Données", dtype=str, keep_default_na=False)
    else:
        frame = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return frame.where(frame != "", None)
