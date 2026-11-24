"""Mode générique : n'importe quel fichier tabulaire, sans schéma bancaire."""

from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from sidadr.analysis import analyze
from sidadr.anomaly_detection import detect_anomalies
from sidadr.config import AnomalySettings, DuplicateSettings, FieldSpec, ImportSettings, Schema
from sidadr.correction import CorrectionError, CorrectionService
from sidadr.deduplication import DuplicateKind, detect_duplicates
from sidadr.export import export_session
from sidadr.ingestion import ImportSession, MappingError
from sidadr.ingestion.generic import field_name, infer_generic_schema, suggest_mode
from sidadr.ingestion.readers import read_table
from sidadr.storage import Workspace

INVOICES = (
    "N° facture;Fournisseur;SIRET;Date facture;Montant HT;Pays;Commentaire\n"
    + "".join(
        f"F-{i:03d};ACME;0123{i:05d};{1 + i % 28:02d}/0{1 + i % 9}/2025;{1000 + 7 * i},50;FR;\n"
        for i in range(60)
    )
    + "F-001;ACME;012300001;02/02/2025;9999,00;FR;\n"  # identifiant en double
    + "F-010;;012300010;11/02/2025;1070,50;FR;\n"  # fournisseur manquant (et identifiant en double)
    + "F-099;ACME;012300099;30/02/2025;250000,00;FR;\n"  # date impossible, montant atypique
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- noms et types


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Date d'opération", "date_d_operation"),
        ("N° client", "n_client"),
        ("2025 CA", "c_2025_ca"),
        ("***", "colonne"),
    ],
)
def test_field_names(header: str, expected: str) -> None:
    assert field_name(header) == expected


def test_types_are_inferred_and_identifiers_stay_text(tmp_path: Path) -> None:
    table = read_table(_write(tmp_path, "f.csv", INVOICES), ImportSettings())
    generic = infer_generic_schema([table])
    types = {f.name: f.type for f in generic.fields}
    assert types == {
        "n_facture": "text",
        "fournisseur": "text",
        "siret": "text",  # numérique, mais identifiant (nom et zéros en tête)
        "date_facture": "date",
        "montant_ht": "decimal",
        "pays": "text",
        "commentaire": "text",
    }
    assert next(f for f in generic.fields if f.name == "commentaire").reason == "colonne vide"
    assert not any(f.required for f in generic.schema.fields)


def test_columns_of_several_files_are_aligned_by_name(tmp_path: Path) -> None:
    a = read_table(_write(tmp_path, "a.csv", "Code;Montant\nX;10\nY;20\n"), ImportSettings())
    b = read_table(_write(tmp_path, "b.csv", "code;Libellé;montant\nZ;test;30\n"), ImportSettings())
    generic = infer_generic_schema([a, b])
    assert [f.name for f in generic.fields] == ["code", "montant", "libelle"]
    assert next(f for f in generic.fields if f.name == "libelle").sources == ("b.csv",)
    session = ImportSession(schema=generic.schema)
    for table, raw in (("a.csv", a), ("b.csv", b)):
        inspection = session.inspect(tmp_path / table)
        session.add(inspection, generic.mappings[raw.source_file].confirm(generic.schema))
    frame = session.frame
    assert list(frame["code"]) == ["X", "Y", "Z"] and list(frame["libelle"]) == [None, None, "test"]


def test_required_fields_and_resolution(tmp_path: Path) -> None:
    generic = infer_generic_schema(
        [read_table(_write(tmp_path, "f.csv", INVOICES), ImportSettings())]
    )
    marked = generic.with_options(["Fournisseur", "montant_ht"])
    assert marked.schema.required == ("fournisseur", "montant_ht")
    assert marked.resolve("N° facture") == "n_facture"
    with pytest.raises(MappingError, match="Colonne inconnue"):
        marked.resolve("Inexistante")


def test_types_chosen_at_import_override_inference(tmp_path: Path) -> None:
    text = (
        "Code postal;Quantité;Livraison\n"
        "01000;3;2025-01-02\n"
        "75001;beaucoup;bientôt\n"
        "13001;5;2025-03-04\n"
    )
    raw = read_table(_write(tmp_path, "f.csv", text), ImportSettings())
    generic = infer_generic_schema([raw])
    assert [f.type for f in generic.schema.fields] == ["text", "text", "text"]

    chosen = generic.with_options(types={"Quantité": "decimal", "livraison": "date"})
    assert [f.type for f in chosen.schema.fields] == ["text", "decimal", "date"]
    assert {f.name: f.reason for f in chosen.fields}["quantite"] == "type choisi à l'import"
    with pytest.raises(MappingError, match="non disponible"):
        generic.with_options(types={"Quantité": "enum"})

    # Une valeur non conforme au type imposé est conservée et signalée, jamais écartée.
    session = ImportSession(schema=chosen.schema)
    inspection = session.inspect(tmp_path / "f.csv")
    session.add(inspection, chosen.mappings["f.csv"].confirm(chosen.schema))
    result = analyze(session.frame, chosen.schema, mode="generic")
    quantity = result.profile["quantite"]
    assert len(session.frame) == 3 and quantity.invalid == 1 and quantity.filled == 3


def test_mode_suggestion() -> None:
    assert suggest_mode({"a.csv": [], "b.csv": []}) == "banking"
    assert suggest_mode({"a.csv": [], "b.csv": ["amount"]}) == "generic"


# ---------------------------------------------------------------- détecteurs sans clé ni groupe


def test_duplicates_without_key_compare_full_rows() -> None:
    rows = [
        {"record_id": f"a:{i}", "import_id": "a", "source_file": "a.csv", "source_row": i, "x": v}
        for i, v in enumerate(["1", "1", "2"])
    ]
    generic_schema = Schema((FieldSpec("x", "x", "text", False, ()),))
    report = detect_duplicates(
        pd.DataFrame(rows, dtype=object), DuplicateSettings((), False, ()), generic_schema
    )
    (group,) = report.groups
    assert group.kind is DuplicateKind.EXACT_ROW and group.record_ids == ("a:0", "a:1")
    assert "Aucune clé" in report.notes[0]


def test_anomalies_without_grouping_use_the_whole_column() -> None:
    values = [str(1000 + (i % 17) * 13) for i in range(200)] + ["900000"]
    rows = [
        {"record_id": f"a:{i}", "import_id": "a", "source_file": "a.csv", "source_row": i, "m": v}
        for i, v in enumerate(values)
    ]
    settings = AnomalySettings(fields=("m",), group_by=())
    (anomaly,) = detect_anomalies(pd.DataFrame(rows, dtype=object), settings).anomalies
    assert anomaly.record_id == "a:200" and anomaly.group == ("ensemble",)
    assert "médiane de la colonne" in anomaly.explanation(settings)


# ---------------------------------------------------------------- session générique complète


@pytest.fixture
def generic_service(tmp_path: Path) -> CorrectionService:
    path = _write(tmp_path, "factures.csv", INVOICES)
    probe = ImportSession()
    inspection = probe.inspect(path)
    generic = infer_generic_schema([inspection.raw]).with_options(["Fournisseur"])
    session = ImportSession(schema=generic.schema)
    session.add(inspection, generic.mappings["factures.csv"].confirm(generic.schema))
    store = Workspace(tmp_path / "ws").create_session(
        session, "factures", "generic", {"key_fields": ["n_facture"], "group_by": []}
    )
    return CorrectionService(store)


def test_generic_session_keeps_its_own_schema(generic_service: CorrectionService) -> None:
    store = generic_service.store
    assert store.mode == "generic" and store.options["key_fields"] == ["n_facture"]
    assert generic_service.schema.names[:2] == ("n_facture", "fournisseur")
    assert generic_service.schema.required == ("fournisseur",)


def test_generic_analysis_finds_the_defects(generic_service: CorrectionService) -> None:
    result = analyze(
        generic_service.working_frame(),
        generic_service.schema,
        mode="generic",
        options=generic_service.store.options,
    )
    assert result.names is None and result.entities is None
    key_groups = {g.key_value for g in result.duplicates.by_kind(DuplicateKind.KEY)}
    assert key_groups == {"F-001", "F-010"}
    # Fournisseur manquant, et la date impossible devient un constat (règle R-DATE).
    assert [(v.rule.rule_id, v.field) for v in result.rules.violations] == [
        ("R-MANDATORY", "fournisseur"),
        ("R-DATE", "date_facture"),
    ]
    assert result.profile["date_facture"].invalid == 1
    assert any(a.raw_value == "250000,00" for a in result.anomalies.anomalies)
    score = result.score
    assert score["consistency"].evaluated is False  # aucune règle de cohérence fournie
    assert score["completeness"].denominator == len(
        generic_service.working_frame()
    )  # seul « fournisseur » est obligatoire


def test_generic_corrections_follow_the_inferred_types(
    generic_service: CorrectionService, tmp_path: Path
) -> None:
    with pytest.raises(CorrectionError, match="nombre"):
        generic_service.correct("factures:1", "montant_ht", "beaucoup", "motif valable")
    with pytest.raises(CorrectionError, match="obligatoire"):
        generic_service.correct("factures:1", "fournisseur", "", "motif valable")
    generic_service.correct("factures:62", "fournisseur", "ACME", "retrouvé sur la facture")
    result = export_session(generic_service, tmp_path / "out", "csv")
    assert any(p.name.endswith("_donnees.csv") for p in result.files)


def test_banking_sessions_are_unchanged(tmp_path: Path) -> None:
    store = Workspace(tmp_path / "ws")
    with pytest.raises(Exception, match="Mode inconnu"):
        store.create_session(ImportSession(), "x", "autre")


# ---------------------------------------------------------------- valeurs de remplissage et formats


def test_placeholders_count_as_missing_and_invalid_values_become_findings(tmp_path: Path) -> None:
    text = (
        "Ref;Article;Quantité;Date\n"
        "T1;Café;2;2025-01-02\n"
        "T2;UNKNOWN;error;2025-01-03\n"
        "T3;Thé;beaucoup;2025-02-30\n"
        "T4;;3;N/A\n"
        "UNKNOWN;Gâteau;1;2025-01-05\n"
        "unknown;Gâteau;1;2025-01-06\n"
    )
    raw = read_table(_write(tmp_path, "f.csv", text), ImportSettings())
    generic = infer_generic_schema([raw]).with_options(
        ["Ref", "Article", "Quantité", "Date"], {"Quantité": "decimal", "Date": "date"}
    )
    session = ImportSession(schema=generic.schema)
    session.add(
        session.inspect(tmp_path / "f.csv"), generic.mappings["f.csv"].confirm(generic.schema)
    )
    result = analyze(session.frame, generic.schema, mode="generic", options={"key_fields": ["ref"]})

    article = result.profile["article"]
    assert (article.missing, article.placeholders) == (2, {"UNKNOWN": 1})
    quantity, when = result.profile["quantite"], result.profile["date"]
    assert (quantity.missing, quantity.invalid) == (1, 1)  # « error » absent, « beaucoup » invalide
    assert (when.missing, when.invalid) == (1, 1)  # « N/A » absent, 30 février invalide

    findings = result.findings()
    by_rule = Counter(f.rule_id for f in findings)
    assert by_rule["R-MANDATORY"] == 6  # article ×2, quantité, date, et les deux réf. « unknown »
    assert by_rule["R-NUMBER"] == 1 and by_rule["R-DATE"] == 1
    shown = next(f for f in findings if f.rule_id == "R-MANDATORY" and f.field == "article")
    assert "« UNKNOWN »" in shown.explanation
    assert next(f for f in findings if f.rule_id == "R-NUMBER").observed_value == "beaucoup"
    # Deux références « UNKNOWN » / « unknown » ne forment pas un doublon d'identifiant.
    assert by_rule.get("DUP-KEY", 0) == 0 and not result.duplicates.findings()


def test_placeholders_are_not_applied_in_banking_mode() -> None:
    from sidadr.config import default_schema

    schema = default_schema()
    row = {name: "x" for name in schema.names}
    frame = pd.DataFrame(
        [
            {
                **row,
                "record_id": "a:1",
                "import_id": "a",
                "source_file": "a.csv",
                "source_row": 1,
                "customer_name": "UNKNOWN",
            }
        ]
    )
    assert analyze(frame, schema).profile["customer_name"].missing == 0
