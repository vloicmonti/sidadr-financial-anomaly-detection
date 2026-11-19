"""Écriture du jeu synthétique : un fichier par banque aux formats volontairement hétérogènes,
des référentiels, et la vérité terrain (manifeste des défauts, identité réelle des clients).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from sidadr import __version__
from sidadr.synthetic.generator import (
    CURRENCIES,
    FIELDS,
    GENERATOR_VERSION,
    CellValue,
    GeneratedDataset,
    InvalidDate,
)

PHONE_COUNTRY_CODE = "999"  # indicatif réservé par l'UIT, non attribué : aucun numéro réel possible


@dataclass(frozen=True)
class FileFormat:
    """Description du format d'export d'un système source."""

    file_name: str
    headers: dict[str, str]  # champ canonique -> en-tête dans le fichier
    kind: str  # "csv" ou "xlsx"
    delimiter: str = ","
    encoding: str = "utf-8"
    date_format: str = "%Y-%m-%d"
    decimal_separator: str = "."
    phone: Callable[[str], str] = lambda digits: digits
    extra_empty_columns: tuple[str, ...] = ()
    sheet_name: str = "Sheet1"


FORMATS: dict[str, FileFormat] = {
    "BALP": FileFormat(
        file_name="alpha_T2.csv",
        kind="csv",
        delimiter=";",
        encoding="utf-8-sig",
        date_format="%d/%m/%Y",
        decimal_separator=",",
        phone=lambda d: f"+{PHONE_COUNTRY_CODE} {d[0:3]} {d[3:5]} {d[5:7]} {d[7:9]}",
        headers={
            "transaction_id": "Ref_Operation", "bank_code": "Code_Banque",
            "branch_code": "Code_Agence", "branch_city": "Ville_Agence",
            "customer_id": "Num_Client", "customer_name": "Nom_Client",
            "birth_date": "Date_Naissance", "phone": "Telephone", "account_number": "Num_Compte",
            "operation_type": "Type_Operation", "transaction_date": "Date_Operation",
            "value_date": "Date_Valeur", "amount": "Montant", "currency": "Devise",
        },
    ),
    "BBET": FileFormat(
        file_name="beta_T2.xlsx",
        kind="xlsx",
        sheet_name="Opérations",
        phone=lambda d: d,
        headers={
            "transaction_id": "Transaction ID", "bank_code": "Bank", "branch_code": "Branch",
            "branch_city": "Branch City", "customer_id": "Customer ID",
            "customer_name": "Customer_Name", "birth_date": "Birth Date", "phone": "Phone",
            "account_number": "Account Number", "operation_type": "Operation",
            "transaction_date": "Transaction_Date", "value_date": "Value Date",
            "amount": "Amount", "currency": "Currency",
        },
    ),
    "BGAM": FileFormat(
        file_name="gamma_T2.csv",
        kind="csv",
        delimiter=",",
        encoding="cp1252",
        phone=lambda d: f"00{PHONE_COUNTRY_CODE}{d}",
        extra_empty_columns=("commentaire",),
        headers={
            "transaction_id": "txn_id", "bank_code": "banque", "branch_code": "agence",
            "branch_city": "ville", "customer_id": "client_id", "customer_name": "Client",
            "birth_date": "naissance", "phone": "tel", "account_number": "compte",
            "operation_type": "type_op", "transaction_date": "date",
            "value_date": "date_valeur", "amount": "montant", "currency": "devise",
        },
    ),
}  # fmt: skip

#: Horodatage figé des métadonnées XLSX (le contenu des cellules est ainsi reproductible).
_FIXED_TIMESTAMP = datetime(2025, 7, 1, 0, 0, 0)


def _text(value: CellValue, fmt: FileFormat, field_name: str) -> str:
    """Rend une valeur pour un fichier CSV selon les conventions de la source."""
    if value is None:
        return ""
    if isinstance(value, InvalidDate):
        if fmt.date_format == "%d/%m/%Y":
            return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"
        return value.iso()
    if isinstance(value, date):
        return value.strftime(fmt.date_format)
    if isinstance(value, Decimal):
        return str(value).replace(".", fmt.decimal_separator)
    if field_name == "phone":
        return fmt.phone(value)
    return value


def _cell(value: CellValue, fmt: FileFormat, field_name: str) -> object:
    """Rend une valeur pour une cellule Excel : dates et nombres natifs, le reste en texte."""
    if value is None or isinstance(value, (date, Decimal)):
        return value
    if isinstance(value, InvalidDate):
        return value.iso()
    if field_name == "phone":
        return fmt.phone(value)
    return value


def canonical(value: CellValue) -> str:
    """Représentation neutre utilisée dans le manifeste."""
    if value is None:
        return ""
    if isinstance(value, InvalidDate):
        return value.iso()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write_csv(
    path: Path, header: list[str], rows: list[list[str]], delimiter: str, encoding: str
) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)


def _write_bank_file(out: Path, bank_code: str, dataset: GeneratedDataset) -> None:
    fmt = FORMATS[bank_code]
    rows = dataset.rows_by_bank[bank_code]
    header = [fmt.headers[f] for f in FIELDS] + list(fmt.extra_empty_columns)
    padding = [""] * len(fmt.extra_empty_columns)
    path = out / fmt.file_name

    if fmt.kind == "csv":
        body = [[_text(r.values[f], fmt, f) for f in FIELDS] + padding for r in rows]
        _write_csv(path, header, body, fmt.delimiter, fmt.encoding)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = fmt.sheet_name
    ws.append(header)
    for r in rows:
        ws.append([_cell(r.values[f], fmt, f) for f in FIELDS])
    for column, field_name in zip(ws.iter_cols(min_row=2), FIELDS, strict=True):
        if field_name in ("birth_date", "transaction_date", "value_date"):
            for cell in column:
                if isinstance(cell.value, date):
                    cell.number_format = "yyyy-mm-dd"
    wb.properties.creator = "SIDADR synthetic generator"
    wb.properties.created = _FIXED_TIMESTAMP
    wb.properties.modified = _FIXED_TIMESTAMP
    wb.save(path)


def row_locations(dataset: GeneratedDataset) -> dict[int, tuple[str, int]]:
    """uid -> (fichier, numéro de ligne de données, 1 = première ligne après l'en-tête)."""
    locations: dict[int, tuple[str, int]] = {}
    for bank_code, rows in dataset.rows_by_bank.items():
        for number, row in enumerate(rows, start=1):
            locations[row.uid] = (FORMATS[bank_code].file_name, number)
    return locations


def _write_reference(out: Path, dataset: GeneratedDataset) -> None:
    ref = out / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    _write_csv(
        ref / "banks.csv",
        ["bank_code", "bank_name", "source_file"],
        [[b.code, b.name, FORMATS[b.code].file_name] for b in dataset.banks],
        ",",
        "utf-8",
    )
    _write_csv(
        ref / "branches.csv",
        ["bank_code", "branch_code", "branch_city"],
        [[b.bank_code, b.code, b.city] for b in dataset.branches],
        ",",
        "utf-8",
    )
    _write_csv(
        ref / "currencies.csv", ["currency", "label"], [list(c) for c in CURRENCIES], ",", "utf-8"
    )


def _write_truth(out: Path, dataset: GeneratedDataset) -> None:
    truth = out / "_truth"
    truth.mkdir(parents=True, exist_ok=True)
    where = row_locations(dataset)
    by_uid = {r.uid: r for rows in dataset.rows_by_bank.values() for r in rows}

    manifest = []
    for d in dataset.defects:
        file_name, number = where[d.uid]
        related = ";".join(f"{where[u][0]}:{where[u][1]}" for u in d.related_uids)
        manifest.append([
            d.defect_id, d.defect_type.value, file_name, str(number),
            canonical(by_uid[d.uid].values["transaction_id"]), d.field or "",
            canonical(d.original), canonical(d.injected), related,
        ])  # fmt: skip
    _write_csv(
        truth / "defects_manifest.csv",
        ["defect_id", "defect_type", "file", "row", "transaction_id", "field",
         "original_value", "injected_value", "related_rows"],
        manifest, ",", "utf-8",
    )  # fmt: skip

    _write_csv(
        truth / "customers.csv",
        ["bank_code", "customer_id", "person_id", "recorded_name", "name_variant", "homonym_of"],
        [[c.bank_code, c.customer_id, c.person_id, c.recorded_name, c.name_variant.value,
          c.homonym_of or ""] for c in dataset.customers],
        ",", "utf-8",
    )  # fmt: skip


def write_dataset(dataset: GeneratedDataset, out: Path) -> dict[str, object]:
    """Écrit l'ensemble des fichiers et renvoie les métadonnées (aussi écrites en JSON)."""
    out.mkdir(parents=True, exist_ok=True)
    for bank in dataset.banks:
        _write_bank_file(out, bank.code, dataset)
    _write_reference(out, dataset)
    _write_truth(out, dataset)

    config = dataset.config
    metadata: dict[str, object] = {
        "generator": {"sidadr_version": __version__, "generator_version": GENERATOR_VERSION},
        "seed": config.seed,
        "base_rows": config.rows,
        "total_rows": dataset.total_rows,
        "period": [config.period_start.isoformat(), config.period_end.isoformat()],
        "reference_date": config.reference_date.isoformat(),
        "fee_cap_xaf": str(config.fee_cap_xaf),
        "files": {
            FORMATS[b.code].file_name: len(dataset.rows_by_bank[b.code]) for b in dataset.banks
        },
        "customers": len(dataset.customers),
        "persons": len(dataset.persons),
        "defects": dict(sorted(Counter(d.defect_type.value for d in dataset.defects).items())),
        "notice": (
            "Données entièrement synthétiques. Noms : combinaisons aléatoires de prénoms et noms "
            "courants ; téléphones : indicatif +999 non attribué ; banques et comptes fictifs."
        ),
    }
    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata
