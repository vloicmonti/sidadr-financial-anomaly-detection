import csv
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sidadr.synthetic.generator import FIELDS, GeneratedDataset, GeneratorConfig, generate
from sidadr.synthetic.writers import FORMATS, write_dataset


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory, small_dataset: GeneratedDataset) -> Path:
    out = tmp_path_factory.mktemp("synthetic")
    write_dataset(small_dataset, out)
    return out


def _read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """Lit un fichier de banque en listes de chaînes, quel que soit son format."""
    fmt = next(f for f in FORMATS.values() if f.file_name == path.name)
    if fmt.kind == "xlsx":
        ws = load_workbook(path, read_only=True)[fmt.sheet_name]
        values = [
            ["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)
        ]
        return values[0], values[1:]
    with path.open(encoding=fmt.encoding, newline="") as handle:
        values = list(csv.reader(handle, delimiter=fmt.delimiter))
    return values[0], values[1:]


def test_all_expected_files_are_written(written: Path) -> None:
    expected = {
        "alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv", "metadata.json",
        "reference/banks.csv", "reference/branches.csv", "reference/currencies.csv",
        "_truth/defects_manifest.csv", "_truth/customers.csv",
    }  # fmt: skip
    assert expected <= {
        p.relative_to(written).as_posix() for p in written.rglob("*") if p.is_file()
    }


def test_source_files_are_heterogeneous(written: Path) -> None:
    alpha = (written / "alpha_T2.csv").read_bytes()
    assert alpha.startswith(b"\xef\xbb\xbf")  # UTF-8 avec BOM
    assert b";" in alpha.splitlines()[0]
    gamma_raw = (written / "gamma_T2.csv").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        gamma_raw.decode("utf-8")  # cp1252 : les accents ne sont pas de l'UTF-8 valide
    headers = {
        name: _read_rows(written / name)[0]
        for name in ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")
    }
    assert "Nom_Client" in headers["alpha_T2.csv"]
    assert "Customer_Name" in headers["beta_T2.xlsx"]
    assert "Client" in headers["gamma_T2.csv"]
    assert headers["gamma_T2.csv"][-1] == "commentaire"
    _header, gamma_rows = _read_rows(written / "gamma_T2.csv")
    assert all(row[-1] == "" for row in gamma_rows)


def test_alpha_uses_french_dates_and_decimal_comma(written: Path) -> None:
    header, rows = _read_rows(written / "alpha_T2.csv")
    date_col = header.index("Date_Operation")
    assert rows[0][date_col][2] == "/" and rows[0][date_col][5] == "/"
    amounts = [r[header.index("Montant")] for r in rows if r[header.index("Devise")] == "EUR"]
    assert any("," in a for a in amounts)


def test_manifest_points_to_the_right_rows(written: Path, small_dataset: GeneratedDataset) -> None:
    files = {
        name: _read_rows(written / name)
        for name in ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")
    }
    with (written / "_truth/defects_manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    assert len(manifest) == len(small_dataset.defects)
    for entry in manifest:
        fmt = next(f for f in FORMATS.values() if f.file_name == entry["file"])
        header, rows = files[entry["file"]]
        row = rows[int(entry["row"]) - 1]
        assert row[header.index(fmt.headers["transaction_id"])] == entry["transaction_id"]
        if entry["defect_type"] == "missing_mandatory":
            assert row[header.index(fmt.headers[entry["field"]])] == ""
        for related in filter(None, entry["related_rows"].split(";")):
            rel_file, rel_row = related.split(":")
            rel_fmt = next(f for f in FORMATS.values() if f.file_name == rel_file)
            rel_header, rel_rows = files[rel_file]
            rel_tx = rel_rows[int(rel_row) - 1][rel_header.index(rel_fmt.headers["transaction_id"])]
            assert rel_tx == entry["transaction_id"]


def test_phone_numbers_use_the_unassigned_country_code(written: Path) -> None:
    header, rows = _read_rows(written / "alpha_T2.csv")
    phones = [r[header.index("Telephone")] for r in rows if r[header.index("Telephone")]]
    assert phones and all(p.startswith("+999 ") for p in phones)
    header, rows = _read_rows(written / "gamma_T2.csv")
    phones = [r[header.index("tel")] for r in rows if r[header.index("tel")]]
    assert phones and all(p.startswith("00999") for p in phones)


def test_metadata_matches_the_files(written: Path, small_dataset: GeneratedDataset) -> None:
    metadata = json.loads((written / "metadata.json").read_text(encoding="utf-8"))
    for name, count in metadata["files"].items():
        assert len(_read_rows(written / name)[1]) == count
    assert sum(metadata["defects"].values()) == len(small_dataset.defects)
    assert metadata["total_rows"] == small_dataset.total_rows


def test_written_output_is_reproducible(tmp_path: Path, small_config: GeneratorConfig) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    write_dataset(generate(small_config), a)
    write_dataset(generate(small_config), b)
    for path in a.rglob("*"):
        if path.is_file() and path.suffix != ".xlsx":
            assert path.read_bytes() == (b / path.relative_to(a)).read_bytes(), path
    assert _read_rows(a / "beta_T2.xlsx") == _read_rows(b / "beta_T2.xlsx")


def test_every_canonical_field_has_a_header_in_every_format() -> None:
    for fmt in FORMATS.values():
        assert set(fmt.headers) == set(FIELDS)
