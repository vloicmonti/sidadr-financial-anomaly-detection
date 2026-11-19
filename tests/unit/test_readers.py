import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from sidadr.config import ImportSettings
from sidadr.ingestion.errors import ImportRejected
from sidadr.ingestion.readers import detect_delimiter, detect_encoding, read_table

SETTINGS = ImportSettings()


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


@pytest.mark.parametrize("delimiter", [";", ",", "\t", "|"])
def test_delimiter_detection(delimiter: str) -> None:
    sample = "\n".join(
        delimiter.join(["a", "b,c" if delimiter != "," else "b", "d"]) for _ in range(5)
    )
    assert detect_delimiter(sample) == delimiter


def test_encoding_detection() -> None:
    assert detect_encoding("Hélène".encode()) == "utf-8-sig"
    assert detect_encoding("Hélène".encode("cp1252")) == "cp1252"


def test_csv_reading_keeps_raw_text_and_reports_problems(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "f.csv",
        "﻿Nom;Montant;Nom;\nKAMGA Hélène;1 250,50;x;\n\n;;;\nTROP;DE;VALEURS;ICI;!\n",
        "utf-8",
    )
    table = read_table(path, SETTINGS)
    assert table.headers == ["Nom", "Montant", "Nom_2", "colonne_4"]
    assert table.rows == [["KAMGA Hélène", "1 250,50", "x", None]]
    assert table.source_rows == [1]
    assert table.skipped_blank_rows == 2
    assert [r.source_row for r in table.rejected] == [4]
    assert any("double" in w for w in table.warnings)


def test_cp1252_file_is_decoded(tmp_path: Path) -> None:
    path = _write(tmp_path / "f.csv", "Client,Ville\nHÉLÈNE,Yaoundé\n", "cp1252")
    table = read_table(path, SETTINGS)
    assert table.encoding == "cp1252"
    assert table.rows[0] == ["HÉLÈNE", "Yaoundé"]


def test_xlsx_reading_converts_cells_to_neutral_text(tmp_path: Path) -> None:
    from datetime import date

    wb = Workbook()
    ws = wb.active
    ws.title = "Opérations"
    ws.append(["Date", "Montant", "Nom"])
    ws.append([date(2025, 4, 1), 150000.0, "MANGA"])
    ws.append([None, None, None])
    ws.append(["2025-02-30", 12.5, None])
    wb.create_sheet("Autre")
    path = tmp_path / "f.xlsx"
    wb.save(path)

    table = read_table(path, SETTINGS)
    assert table.sheet == "Opérations"
    assert table.rows == [["2025-04-01", "150000", "MANGA"], ["2025-02-30", "12.5", None]]
    assert table.source_rows == [1, 3]
    assert any("2 feuilles" in w for w in table.warnings)
    with pytest.raises(ImportRejected) as error:
        read_table(path, SETTINGS, sheet="Absente")
    assert error.value.code == "sheet_not_found"


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("f.txt", b"a,b\n1,2\n", "extension"),
        ("f.csv", b"", "empty_file"),
        ("f.csv", b"a,b\x00\x01\x02", "binary_csv"),
        ("f.xlsx", b"a;b\n1;2\n", "bad_signature"),
        ("f.csv", b"\n\n  \n", "no_content"),
    ],
)
def test_malformed_files_are_rejected_with_a_clear_code(
    tmp_path: Path, name: str, content: bytes, code: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(ImportRejected) as error:
        read_table(path, SETTINGS)
    assert error.value.code == code


def test_missing_file_and_size_limit(tmp_path: Path) -> None:
    with pytest.raises(ImportRejected, match="introuvable"):
        read_table(tmp_path / "absent.csv", SETTINGS)
    path = _write(tmp_path / "f.csv", "a,b\n" + "1,2\n" * 1000)
    with pytest.raises(ImportRejected) as error:
        read_table(path, ImportSettings(max_file_mb=0.001))
    assert error.value.code == "too_large"


def test_row_limit(tmp_path: Path) -> None:
    path = _write(tmp_path / "f.csv", "a,b\n" + "1,2\n" * 20)
    with pytest.raises(ImportRejected) as error:
        read_table(path, ImportSettings(max_rows=10))
    assert error.value.code == "too_many_rows"


def test_zip_bomb_disguised_as_xlsx_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/huge.xml", b"0" * 5_000_000)
    with pytest.raises(ImportRejected) as error:
        read_table(path, SETTINGS)
    assert error.value.code == "zip_bomb"


def test_corrupted_xlsx_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"PK\x03\x04" + b"garbage" * 100)
    with pytest.raises(ImportRejected) as error:
        read_table(path, SETTINGS)
    assert error.value.code in {"bad_xlsx"}
