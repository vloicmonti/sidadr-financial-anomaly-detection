"""Lecture sécurisée des fichiers CSV et XLSX vers une table brute de chaînes.

Toutes les valeurs sont lues comme du texte, sans interprétation : l'inférence de
types se fait ensuite (module `values`), colonne par colonne, de façon explicable.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook

from sidadr.config import ImportSettings
from sidadr.ingestion.errors import ImportRejected

CANDIDATE_DELIMITERS: tuple[str, ...] = (";", ",", "\t", "|")
CANDIDATE_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252")

FileKind = Literal["csv", "xlsx"]


@dataclass(frozen=True)
class RejectedRow:
    source_row: int  # numéro de ligne de données (1 = première ligne après l'en-tête)
    reason: str


@dataclass
class RawTable:
    """Contenu brut d'un fichier : en-têtes et lignes de chaînes (None = cellule vide)."""

    source_file: str
    kind: FileKind
    headers: list[str]
    rows: list[list[str | None]]
    source_rows: list[int]  # numéro de ligne de données de chaque ligne conservée
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    rejected: list[RejectedRow] = field(default_factory=list)
    skipped_blank_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def column(self, header: str) -> list[str | None]:
        index = self.headers.index(header)
        return [row[index] for row in self.rows]


# --------------------------------------------------------------------------- contrôles


def check_file(path: Path, settings: ImportSettings) -> FileKind:
    """Contrôles avant lecture : existence, extension, taille, signature."""
    if not path.is_file():
        raise ImportRejected("not_found", f"Fichier introuvable : {path.name}")
    suffix = path.suffix.lower()
    if suffix not in settings.allowed_extensions:
        allowed = ", ".join(settings.allowed_extensions)
        raise ImportRejected(
            "extension", f"Extension {suffix or '(aucune)'} refusée. Acceptées : {allowed}."
        )
    size = path.stat().st_size
    if size == 0:
        raise ImportRejected("empty_file", f"{path.name} est vide.")
    if size > settings.max_file_mb * 1024 * 1024:
        raise ImportRejected(
            "too_large", f"{path.name} dépasse la taille maximale de {settings.max_file_mb:g} Mo."
        )
    with path.open("rb") as handle:
        head = handle.read(8)
    if suffix == ".xlsx":
        if not head.startswith(b"PK\x03\x04"):
            raise ImportRejected(
                "bad_signature", f"{path.name} n'est pas un classeur Excel (.xlsx) valide."
            )
        _check_zip(path, settings)
        return "xlsx"
    if head.startswith(b"PK\x03\x04") or b"\x00" in head:
        raise ImportRejected(
            "binary_csv", f"{path.name} semble binaire : ce n'est pas un fichier CSV texte."
        )
    return "csv"


def _check_zip(path: Path, settings: ImportSettings) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ImportRejected("bad_xlsx", f"{path.name} est un classeur corrompu.") from exc
    uncompressed = sum(i.file_size for i in infos)
    compressed = max(1, sum(i.compress_size for i in infos))
    if uncompressed > settings.xlsx_max_uncompressed_mb * 1024 * 1024:
        raise ImportRejected(
            "xlsx_too_large", f"{path.name} est trop volumineux une fois décompressé."
        )
    if uncompressed / compressed > settings.xlsx_max_compression_ratio:
        raise ImportRejected(
            "zip_bomb", f"{path.name} a un taux de compression anormal : import refusé."
        )


# --------------------------------------------------------------------------- CSV


def detect_encoding(data: bytes) -> str:
    """UTF-8 (avec ou sans BOM) si le contenu est de l'UTF-8 valide, sinon Windows-1252.

    Règle simple et explicable : un texte accentué en cp1252 n'est presque jamais de
    l'UTF-8 valide, alors que l'inverse est fréquent. Latin-1 sert de dernier recours
    (il décode tout octet) et produit un avertissement.
    """
    for encoding in CANDIDATE_ENCODINGS:
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    return "latin-1"


def detect_delimiter(sample: str) -> str:
    """Choisit le séparateur qui donne le même nombre de colonnes (> 1) sur le plus de lignes."""
    lines = [line for line in sample.splitlines()[:50] if line.strip()]
    if not lines:
        raise ImportRejected("no_content", "Aucune ligne exploitable dans le fichier.")
    best, best_score = ",", (-1, -1)
    for delimiter in CANDIDATE_DELIMITERS:
        widths = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        width, frequency = Counter(widths).most_common(1)[0]
        score = (frequency, width) if width > 1 else (0, 0)
        if score > best_score:
            best, best_score = delimiter, score
    if best_score[1] <= 1:
        raise ImportRejected("no_delimiter", "Impossible d'identifier le séparateur de colonnes.")
    return best


def _clean_headers(raw: list[str], warnings: list[str]) -> list[str]:
    headers: list[str] = []
    for i, name in enumerate(raw, start=1):
        label = name.strip().lstrip("﻿") or f"colonne_{i}"
        if label != name.strip():
            warnings.append(f"En-tête vide en position {i} : nommé « {label} ».")
        base, n = label, 2
        while label in headers:
            label = f"{base}_{n}"
            n += 1
        if label != base:
            warnings.append(f"En-tête « {base} » en double : renommé « {label} ».")
        headers.append(label)
    return headers


def _cell(text: str) -> str | None:
    return text if text.strip() else None


def read_csv(path: Path, settings: ImportSettings) -> RawTable:
    data = path.read_bytes()
    encoding = detect_encoding(data)
    text = data.decode(encoding)
    warnings: list[str] = []
    if encoding == "latin-1":
        warnings.append(
            "Encodage non reconnu (ni UTF-8 ni Windows-1252) : lu en Latin-1, accents à vérifier."
        )
    delimiter = detect_delimiter(text[: settings.csv_sample_bytes])

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        records = list(reader)
    except csv.Error as exc:
        raise ImportRejected("malformed_csv", f"CSV malformé : {exc}") from exc

    start = next((i for i, r in enumerate(records) if any(c.strip() for c in r)), None)
    if start is None:
        raise ImportRejected("no_content", f"{path.name} ne contient aucune donnée.")
    headers = _clean_headers(records[start], warnings)
    if len(headers) > settings.max_columns:
        raise ImportRejected(
            "too_many_columns", f"{len(headers)} colonnes : maximum {settings.max_columns}."
        )

    table = RawTable(
        path.name, "csv", headers, [], [], encoding=encoding, delimiter=delimiter, warnings=warnings
    )
    for number, record in enumerate(records[start + 1 :], start=1):
        if not any(c.strip() for c in record):
            table.skipped_blank_rows += 1
            continue
        if len(record) != len(headers):
            table.rejected.append(
                RejectedRow(number, f"{len(record)} valeurs pour {len(headers)} colonnes attendues")
            )
            continue
        table.rows.append([_cell(c) for c in record])
        table.source_rows.append(number)
        if table.n_rows > settings.max_rows:
            raise ImportRejected(
                "too_many_rows", f"Plus de {settings.max_rows} lignes : import refusé."
            )
    return table


# --------------------------------------------------------------------------- XLSX


def _xlsx_text(value: object) -> str | None:
    """Convertit une cellule Excel en texte neutre (dates ISO, nombres sans notation exotique)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _cell(str(value))


def read_xlsx(path: Path, settings: ImportSettings, sheet: str | None = None) -> RawTable:
    try:
        # read_only : lecture en flux ; les formules ne sont jamais évaluées.
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl lève des types variés sur fichier corrompu
        raise ImportRejected("bad_xlsx", f"{path.name} ne peut pas être ouvert : {exc}") from exc
    try:
        names = workbook.sheetnames
        if sheet is not None and sheet not in names:
            raise ImportRejected(
                "sheet_not_found", f"Feuille « {sheet} » absente. Feuilles : {', '.join(names)}."
            )
        warnings: list[str] = []
        chosen = sheet or names[0]
        if sheet is None and len(names) > 1:
            warnings.append(f"Classeur à {len(names)} feuilles : seule « {chosen} » est importée.")
        records = [
            [_xlsx_text(v) for v in row] for row in workbook[chosen].iter_rows(values_only=True)
        ]
    finally:
        workbook.close()

    start = next((i for i, r in enumerate(records) if any(c is not None for c in r)), None)
    if start is None:
        raise ImportRejected("no_content", f"La feuille « {chosen} » est vide.")
    raw_headers = records[start]
    while raw_headers and raw_headers[-1] is None:  # colonnes de fin sans en-tête
        raw_headers = raw_headers[:-1]
    headers = _clean_headers([h or "" for h in raw_headers], warnings)
    if len(headers) > settings.max_columns:
        raise ImportRejected(
            "too_many_columns", f"{len(headers)} colonnes : maximum {settings.max_columns}."
        )

    table = RawTable(path.name, "xlsx", headers, [], [], sheet=chosen, warnings=warnings)
    width = len(headers)
    for number, record in enumerate(records[start + 1 :], start=1):
        values = (record + [None] * width)[:width]
        if any(c is not None for c in record[width:]):
            table.rejected.append(
                RejectedRow(number, "valeurs au-delà de la dernière colonne nommée")
            )
            continue
        if all(c is None for c in values):
            table.skipped_blank_rows += 1
            continue
        table.rows.append(values)
        table.source_rows.append(number)
        if table.n_rows > settings.max_rows:
            raise ImportRejected(
                "too_many_rows", f"Plus de {settings.max_rows} lignes : import refusé."
            )
    return table


def read_table(path: Path, settings: ImportSettings, sheet: str | None = None) -> RawTable:
    """Point d'entrée : contrôle puis lit un fichier CSV ou XLSX."""
    kind = check_file(path, settings)
    return read_xlsx(path, settings, sheet) if kind == "xlsx" else read_csv(path, settings)
