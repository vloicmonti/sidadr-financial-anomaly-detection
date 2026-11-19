"""Import en deux temps : inspection (lecture + proposition de correspondance), puis
chargement une fois la correspondance confirmée par l'utilisateur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from sidadr.config import Schema, Settings, default_schema, default_settings
from sidadr.ingestion.errors import ImportRejected, MappingError
from sidadr.ingestion.mapping import ColumnMapping, propose_mapping
from sidadr.ingestion.readers import RawTable, RejectedRow, read_table
from sidadr.ingestion.values import TypeInference, infer_type

TECHNICAL_COLUMNS: tuple[str, ...] = ("record_id", "import_id", "source_file", "source_row")


@dataclass
class Inspection:
    """Résultat de la lecture d'un fichier, avant toute décision de l'utilisateur."""

    raw: RawTable
    mapping: ColumnMapping  # proposée, non confirmée
    column_types: dict[str, TypeInference]
    empty_columns: list[str]

    @property
    def source_file(self) -> str:
        return self.raw.source_file


@dataclass
class ImportReport:
    source_file: str
    import_id: str
    kind: str
    encoding: str | None
    delimiter: str | None
    sheet: str | None
    rows_imported: int
    rows_rejected: list[RejectedRow]
    skipped_blank_rows: int
    empty_columns: list[str]
    unmapped_columns: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportedTable:
    import_id: str
    raw: RawTable  # contenu original, conservé tel quel
    mapping: ColumnMapping
    frame: pd.DataFrame  # colonnes techniques + champs canoniques, valeurs texte d'origine
    report: ImportReport


def inspect_file(
    path: Path,
    schema: Schema | None = None,
    settings: Settings | None = None,
    sheet: str | None = None,
) -> Inspection:
    schema = schema or default_schema()
    settings = settings or default_settings()
    raw = read_table(path, settings.imports, sheet)
    types = {h: infer_type(raw.column(h)) for h in raw.headers}
    empty = [h for h, t in types.items() if t.non_empty == 0]
    return Inspection(raw, propose_mapping(raw.source_file, raw.headers, schema), types, empty)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_") or "import"


def build_table(
    inspection: Inspection, mapping: ColumnMapping, import_id: str, schema: Schema
) -> ImportedTable:
    """Construit la table canonique à partir d'une correspondance confirmée."""
    if not mapping.confirmed:
        raise MappingError("La correspondance des colonnes doit être confirmée avant l'import.")
    if mapping.source_file != inspection.source_file:
        raise MappingError("Cette correspondance concerne un autre fichier.")
    mapping.validate(schema)

    raw = inspection.raw
    columns: dict[str, list[object]] = {
        "record_id": [f"{import_id}:{n}" for n in raw.source_rows],
        "import_id": [import_id] * raw.n_rows,
        "source_file": [raw.source_file] * raw.n_rows,
        "source_row": list(raw.source_rows),
    }
    by_target = mapping.target_to_source()
    for name in schema.names:
        source = by_target.get(name)
        columns[name] = list(raw.column(source)) if source else [None] * raw.n_rows
    frame = pd.DataFrame(columns, dtype=object)
    frame["source_row"] = frame["source_row"].astype("int64")

    warnings = list(raw.warnings)
    absent_optional = [n for n in schema.names if n not in by_target and n not in schema.required]
    if absent_optional:
        warnings.append(f"Champs facultatifs absents du fichier : {', '.join(absent_optional)}.")
    report = ImportReport(
        source_file=raw.source_file,
        import_id=import_id,
        kind=raw.kind,
        encoding=raw.encoding,
        delimiter=raw.delimiter,
        sheet=raw.sheet,
        rows_imported=raw.n_rows,
        rows_rejected=list(raw.rejected),
        skipped_blank_rows=raw.skipped_blank_rows,
        empty_columns=list(inspection.empty_columns),
        unmapped_columns=mapping.unmapped_sources(),
        warnings=warnings,
    )
    return ImportedTable(import_id, raw, mapping, frame, report)


@dataclass
class ImportSession:
    """Ensemble des fichiers importés dans une même session d'analyse."""

    schema: Schema = field(default_factory=default_schema)
    settings: Settings = field(default_factory=default_settings)
    tables: list[ImportedTable] = field(default_factory=list)

    def inspect(self, path: Path, sheet: str | None = None) -> Inspection:
        return inspect_file(path, self.schema, self.settings, sheet)

    def add(self, inspection: Inspection, mapping: ColumnMapping) -> ImportedTable:
        base = _slug(inspection.source_file)
        existing = {t.import_id for t in self.tables}
        import_id, n = base, 2
        while import_id in existing:
            import_id, n = f"{base}_{n}", n + 1
        table = build_table(inspection, mapping, import_id, self.schema)
        self.tables.append(table)
        return table

    @property
    def frame(self) -> pd.DataFrame:
        if not self.tables:
            raise ImportRejected("empty_session", "Aucun fichier importé dans la session.")
        return pd.concat([t.frame for t in self.tables], ignore_index=True)
