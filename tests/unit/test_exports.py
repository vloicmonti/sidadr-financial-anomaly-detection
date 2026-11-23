import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sidadr.analysis import analyze, findings_to_records, save_findings, save_summary, summarize
from sidadr.correction import CorrectionService, Decision
from sidadr.export import export_session, read_back
from sidadr.ingestion import ImportSession
from sidadr.reporting.html_report import render_report, write_report
from sidadr.storage import Workspace

CSV = (
    "Ref_Operation;Code_Banque;Code_Agence;Num_Client;Nom_Client;Num_Compte;Type_Operation;"
    "Date_Operation;Date_Valeur;Montant;Devise\n"
    'TX-1;BALP;BALP-001;C-1;=HYPERLINK("http://x");A-1;DEPOSIT;01/04/2025;02/04/2025;1000;XAF\n'
    "TX-2;BALP;BALP-001;C-2;<script>alert(1)</script>;A-2;DEPOSIT;25/04/2025;25/04/2025;-2500,50;XAF\n"
    "TX-3;BALP;BALP-002;C-3;ONANA Paul;A-3;FEE;26/04/2025;26/04/2025;750;XAF\n"
    "TX-3;BALP;BALP-002;C-3;ONANA Paul;A-3;FEE;26/04/2025;26/04/2025;750;XAF\n"
)


@pytest.fixture
def service(tmp_path: Path) -> CorrectionService:
    path = tmp_path / "alpha.csv"
    path.write_text(CSV, encoding="utf-8")
    session = ImportSession()
    inspection = session.inspect(path)
    session.add(inspection, inspection.mapping.confirm(session.schema))
    store = Workspace(tmp_path / "ws").create_session(session, "test export")
    return CorrectionService(store, actor="testeur")


def _analyze(service: CorrectionService) -> None:
    result = analyze(service.working_frame(), service.schema)
    records = findings_to_records(result.findings(), service.decisions())
    save_findings(service.store.findings_path, records)
    save_summary(service.store.summary_path, summarize(result, records, service.data_changes()))


def test_xlsx_export_contains_every_sheet_and_writes_text_only(
    service: CorrectionService, tmp_path: Path
) -> None:
    _analyze(service)
    result = export_session(service, tmp_path / "out", "xlsx")
    (path,) = result.files
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Données", "Constats", "Journal", "Traçabilité"]
    cells = [
        c for row in workbook["Données"].iter_rows(min_row=2) for c in row if c.value is not None
    ]
    assert all(c.data_type == "s" for c in cells)  # aucune formule possible
    trace = dict(workbook["Traçabilité"].iter_rows(min_row=2, values_only=True))
    assert trace["session_id"] == service.store.session_id
    assert trace["original_sha256"] == service.store.metadata["original_sha256"]
    assert "configuration : rules/transactions.yaml" in trace


def test_an_xlsx_export_reimports_with_identical_values(
    service: CorrectionService, tmp_path: Path
) -> None:
    service.correct("alpha:3", "amount", "800", "frais corrigés")
    service.exclude("alpha:4", "copie exportée deux fois")
    _analyze(service)
    (path,) = export_session(service, tmp_path / "out", "xlsx").files
    exported = read_back(path)
    working = service.working_frame()
    assert list(exported["record_id"]) == list(working["record_id"])
    for field in ("customer_name", "amount", "transaction_date", "branch_code"):
        assert list(exported[field]) == [None if v is None else str(v) for v in working[field]]
    assert "800" in set(exported["amount"]) and "alpha:4" not in set(exported["record_id"])


def test_csv_export_neutralizes_formulas_but_keeps_negative_amounts(
    service: CorrectionService, tmp_path: Path
) -> None:
    _analyze(service)
    result = export_session(service, tmp_path / "out", "csv")
    names = sorted(p.name for p in result.files)
    assert names == sorted(
        f"sidadr_{service.store.session_id}{s}"
        for s in ("_constats.csv", "_donnees.csv", "_journal.csv", ".meta.json")
    )
    data = read_back(next(p for p in result.files if p.name.endswith("_donnees.csv")))
    names_col = list(data["customer_name"])
    assert names_col[0] == '\'=HYPERLINK("http://x")'
    assert "-2500,50" in set(data["amount"])  # un nombre négatif n'est pas neutralisé
    assert result.neutralized_cells >= 1
    meta = json.loads(
        next(p for p in result.files if p.suffix == ".json").read_text(encoding="utf-8")
    )
    assert meta["neutralized_cells"] == result.neutralized_cells
    assert meta["analysis_up_to_date"] is True


def test_traceability_flags_an_outdated_analysis(
    service: CorrectionService, tmp_path: Path
) -> None:
    _analyze(service)
    service.correct("alpha:3", "amount", "800", "frais corrigés")
    result = export_session(service, tmp_path / "out", "csv")
    assert result.traceability["analysis_up_to_date"] is False
    assert result.traceability["active_corrections"] == 1
    assert result.traceability["journal_integrity"] == "intact"


def test_report_requires_an_analysis(service: CorrectionService) -> None:
    with pytest.raises(ValueError, match="sidadr analyze"):
        render_report(service)


def test_report_is_self_contained_and_escapes_data(
    service: CorrectionService, tmp_path: Path
) -> None:
    _analyze(service)
    records = json.loads(service.store.findings_path.read_text(encoding="utf-8"))
    service.decide(
        records[0]["key"], records[0]["finding_id"], Decision.CONFIRMED, "vérifié à la source"
    )
    path = write_report(service, tmp_path / "rapport.html")
    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "<script" not in html.lower()  # aucun script, aucune ressource externe
    assert "http://" not in html.replace('"http://x"', "")
    for section in (
        "Score qualité",
        "Constats",
        "Règles métier appliquées",
        "Corrections et décisions",
        "Traçabilité",
    ):
        assert section in html
    assert "Chaîne d&#x27;empreintes intacte" in html or "Chaîne d'empreintes intacte" in html
    assert "Confirmé" in html
