"""Import et profilage du jeu de référence, mesurés contre la vérité terrain."""

import csv
from collections import Counter
from pathlib import Path

import pytest

from sidadr.cli import main
from sidadr.ingestion import ImportSession
from sidadr.ingestion.importer import _slug
from sidadr.profiling import DatasetProfile, profile_frame

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
FILES = ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")

pytestmark = pytest.mark.skipif(
    not (DATA / "metadata.json").exists(), reason="jeu de référence absent"
)


@pytest.fixture(scope="module")
def session() -> ImportSession:
    session = ImportSession()
    for name in FILES:
        inspection = session.inspect(DATA / name)
        session.add(inspection, inspection.mapping.confirm(session.schema))
    return session


@pytest.fixture(scope="module")
def profile(session: ImportSession) -> DatasetProfile:
    return profile_frame(session.frame, session.schema, session.settings)


@pytest.fixture(scope="module")
def manifest() -> list[dict[str, str]]:
    with (DATA / "_truth" / "defects_manifest.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _record_id(entry: dict[str, str]) -> str:
    return f"{_slug(entry['file'])}:{entry['row']}"


def test_every_file_is_mapped_automatically_and_completely(session: ImportSession) -> None:
    for table in session.tables:
        assert table.mapping.missing_required(session.schema) == []
        assert table.report.rows_rejected == []
    gamma = next(t for t in session.tables if t.import_id == "gamma_t2")
    assert gamma.report.unmapped_columns == ["commentaire"]
    assert gamma.report.empty_columns == ["commentaire"]


def test_rows_and_record_ids_match_the_files(
    session: ImportSession, manifest: list[dict[str, str]]
) -> None:
    frame = session.frame
    assert len(frame) == sum(t.report.rows_imported for t in session.tables)
    assert frame["record_id"].is_unique
    by_id = frame.set_index("record_id")["transaction_id"]
    for entry in manifest:
        assert by_id[_record_id(entry)] == entry["transaction_id"]


def test_missing_mandatory_values_are_all_found(
    profile: DatasetProfile, manifest: list[dict[str, str]]
) -> None:
    expected = Counter(e["field"] for e in manifest if e["defect_type"] == "missing_mandatory")
    for field_name, count in expected.items():
        assert profile[field_name].missing == count, field_name


@pytest.mark.parametrize(
    ("defect_type", "field_name"),
    [("invalid_date", "transaction_date"), ("amount_not_numeric", "amount")],
)
def test_invalid_values_match_the_manifest_exactly(
    profile: DatasetProfile, manifest: list[dict[str, str]], defect_type: str, field_name: str
) -> None:
    expected = {_record_id(e) for e in manifest if e["defect_type"] == defect_type}
    assert set(profile[field_name].invalid_record_ids) == expected  # rappel et précision = 100 %


def test_exact_duplicate_rows_are_counted(
    profile: DatasetProfile, manifest: list[dict[str, str]]
) -> None:
    exact = [e for e in manifest if e["defect_type"] == "exact_duplicate_row"]
    # Chaque copie forme un groupe avec sa source ; les doublons inter-fichiers dont le
    # téléphone est vide sont aussi des lignes complètes identiques après normalisation.
    assert profile.duplicate_groups >= len(exact)
    assert profile.duplicate_rows >= 2 * len(exact)


def test_cli_requires_explicit_confirmation(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["profile", *files]) == 2
    out = tmp_path / "profile.json"
    assert main(["profile", *files, "--accept-proposed", "--json", str(out)]) == 0
    assert out.exists() and '"score"' in out.read_text(encoding="utf-8")


def test_cli_applies_a_mapping_file(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("gamma_T2.csv:\n  ville: null\n", encoding="utf-8")
    assert main(["profile", str(DATA / "gamma_T2.csv"), "--mapping", str(mapping)]) == 0
    mapping.write_text("gamma_T2.csv:\n  montant: null\n", encoding="utf-8")
    assert main(["profile", str(DATA / "gamma_T2.csv"), "--mapping", str(mapping)]) == 1
