import sqlite3
import stat
from pathlib import Path

import pandas as pd
import pytest

from sidadr.analysis import finding_key
from sidadr.correction import CorrectionError, CorrectionService, Decision
from sidadr.findings import Finding, Severity
from sidadr.ingestion import ImportSession
from sidadr.storage import SessionStore, Workspace, WorkspaceError

CSV = (
    "Ref_Operation;Code_Banque;Code_Agence;Num_Client;Nom_Client;Num_Compte;Type_Operation;"
    "Date_Operation;Date_Valeur;Montant;Devise;Date_Naissance\n"
    "TX-1;BALP;BALP-001;C-1;MANGA Jean;A-1;DEPOSIT;01/04/2025;02/04/2025;1000;XAF;\n"
    "TX-2;BALP;;C-2;KAMGA Hélène;A-2;WITHDRAWAL;25/04/2025;25/04/2025;2500,50;XAF;12/03/1984\n"
    "TX-3;BALP;BALP-002;C-3;ONANA Paul;A-3;FEE;26/04/2025;26/04/2025;750;XAF;\n"
)


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    path = tmp_path / "alpha.csv"
    path.write_text(CSV, encoding="utf-8")
    session = ImportSession()
    inspection = session.inspect(path)
    session.add(inspection, inspection.mapping.confirm(session.schema))
    return Workspace(tmp_path / "ws").create_session(session, "test")


@pytest.fixture
def service(store: SessionStore) -> CorrectionService:
    return CorrectionService(store, actor="testeur", clock=lambda: "2026-01-01T00:00:00+00:00")


# ---------------------------------------------------------------- espace de travail


def test_session_snapshot_is_read_only_and_listed(store: SessionStore, tmp_path: Path) -> None:
    assert not store.original_path.stat().st_mode & stat.S_IWRITE
    assert store.metadata["rows"] == 3
    assert store.metadata["imports"][0]["mapping"]["Nom_Client"] == "customer_name"
    workspace = Workspace(tmp_path / "ws")
    assert [s["session_id"] for s in workspace.list_sessions()] == [store.session_id]
    assert workspace.open_session(store.session_id).metadata == store.metadata


def test_a_tampered_snapshot_blocks_the_session(store: SessionStore) -> None:
    store.original_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    pd.DataFrame({"record_id": ["x"]}).to_parquet(store.original_path)
    with pytest.raises(WorkspaceError, match="modifié"):
        CorrectionService(store)


@pytest.mark.parametrize("session_id", ["../etc", "absente", "S 1"])
def test_unknown_or_unsafe_session_ids_are_refused(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).open_session(session_id)


# ---------------------------------------------------------------- corrections


def test_a_correction_changes_the_working_view_but_never_the_original(
    service: CorrectionService,
) -> None:
    correction_id = service.correct(
        "alpha:2", "branch_code", "BALP-001", "retrouvé dans le système source"
    )
    assert correction_id == 1
    working = service.working_frame().set_index("record_id")
    assert working.at["alpha:2", "branch_code"] == "BALP-001"
    assert service.original_frame().set_index("record_id").at["alpha:2", "branch_code"] is None
    (entry,) = service.history()
    assert (entry.action_type, entry.old_value, entry.new_value, entry.actor) == (
        "correct",
        None,
        "BALP-001",
        "testeur",
    )


@pytest.mark.parametrize(
    ("record_id", "field", "value", "reason", "message"),
    [
        ("alpha:1", "amount", "2000", "", "motif"),
        ("alpha:1", "amount", "2000", "ok", "motif"),
        ("alpha:99", "amount", "2000", "motif valable", "inconnu"),
        ("alpha:1", "inconnu", "x", "motif valable", "Champ inconnu"),
        ("alpha:1", "amount", "abc", "motif valable", "nombre"),
        ("alpha:1", "transaction_date", "2025-02-30", "motif valable", "date valide"),
        ("alpha:1", "operation_type", "VIREMENT", "motif valable", "valeurs"),
        ("alpha:1", "customer_name", "", "motif valable", "obligatoire"),
        ("alpha:1", "amount", "1000", "motif valable", "identique"),
    ],
)
def test_invalid_corrections_are_refused(
    service: CorrectionService, record_id: str, field: str, value: str, reason: str, message: str
) -> None:
    with pytest.raises(CorrectionError, match=message):
        service.correct(record_id, field, value, reason)
    assert service.history() == []


def test_an_optional_field_can_be_cleared(service: CorrectionService) -> None:
    service.correct("alpha:2", "birth_date", "", "date de naissance erronée")
    assert service.working_frame().set_index("record_id").at["alpha:2", "birth_date"] is None


def test_corrections_stack_and_undo_restores_the_previous_value(service: CorrectionService) -> None:
    first = service.correct("alpha:1", "amount", "1100", "premier correctif")
    second = service.correct("alpha:1", "amount", "1200", "second correctif")
    assert service.current_value("alpha:1", "amount") == "1200"
    service.undo(f"C{second}", "second correctif erroné")
    assert service.current_value("alpha:1", "amount") == "1100"
    service.undo(f"C{first}", "retour à l'original")
    assert service.current_value("alpha:1", "amount") == "1000"


def test_undoing_every_correction_restores_the_original_exactly(service: CorrectionService) -> None:
    ids = [
        service.correct("alpha:1", "amount", "1500", "montant corrigé"),
        service.correct("alpha:2", "branch_code", "BALP-002", "agence retrouvée"),
        service.correct("alpha:3", "customer_name", "ONANA Paul Marie", "nom complet"),
    ]
    for correction_id in ids:
        service.undo(f"C{correction_id}", "annulation de test")
    pd.testing.assert_frame_equal(service.working_frame(), service.original_frame())
    assert len(service.history()) == 2 * len(ids)


def test_an_undo_cannot_be_repeated_or_target_nothing(service: CorrectionService) -> None:
    correction_id = service.correct("alpha:1", "amount", "1500", "montant corrigé")
    service.undo(f"c{correction_id}", "annulation")
    with pytest.raises(CorrectionError, match="déjà annulé"):
        service.undo(f"C{correction_id}", "annulation")
    for target in ("C99", "X1", "Cabc"):
        with pytest.raises(CorrectionError):
            service.undo(target, "annulation")


# ---------------------------------------------------------------- exclusions et décisions


def test_exclusion_hides_a_record_without_deleting_it(service: CorrectionService) -> None:
    exclusion = service.exclude("alpha:3", "ligne exportée deux fois")
    assert "alpha:3" not in set(service.working_frame()["record_id"])
    assert "alpha:3" in set(service.working_frame(include_excluded=True)["record_id"])
    with pytest.raises(CorrectionError, match="déjà exclu"):
        service.exclude("alpha:3", "ligne exportée deux fois")
    service.undo(f"C{exclusion}", "exclusion à tort")
    assert "alpha:3" in set(service.working_frame()["record_id"])


def test_a_new_decision_replaces_the_previous_one(service: CorrectionService) -> None:
    service.decide("cle-1", "NAME-00001", Decision.DEFERRED, "à revoir avec l'agence")
    second = service.decide("cle-1", "NAME-00004", Decision.REJECTED, "homonymes confirmés")
    assert service.decisions() == {"cle-1": "rejected"}
    service.undo(f"D{second}", "décision prématurée")
    assert service.decisions() == {}
    assert [e.action_type for e in service.history()] == ["decide", "decide", "undo"]


def test_finding_keys_do_not_depend_on_numbering_or_order() -> None:
    a = Finding(
        "DUP-00001",
        "duplicate_key",
        Severity.CERTAIN_ERROR,
        ("x:1", "x:2"),
        "DUP:transaction_id",
        "",
        "",
    )
    b = Finding(
        "DUP-00042",
        "duplicate_key",
        Severity.CERTAIN_ERROR,
        ("x:2", "x:1"),
        "DUP:transaction_id",
        "",
        "",
    )
    c = Finding(
        "DUP-00001",
        "duplicate_key",
        Severity.CERTAIN_ERROR,
        ("x:1", "x:3"),
        "DUP:transaction_id",
        "",
        "",
    )
    assert finding_key(a) == finding_key(b) != finding_key(c)


# ---------------------------------------------------------------- journal d'audit


def test_the_audit_log_is_append_only(service: CorrectionService) -> None:
    service.correct("alpha:1", "amount", "1500", "montant corrigé")
    with service.store.connect() as connection:
        with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
            connection.execute("UPDATE audit_log SET reason = 'falsifié'")
        with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
            connection.execute("DELETE FROM audit_log")
        with pytest.raises(sqlite3.DatabaseError, match="annulée"):
            connection.execute("DELETE FROM corrections")


def test_tampering_with_the_journal_is_detected(service: CorrectionService) -> None:
    service.correct("alpha:1", "amount", "1500", "montant corrigé")
    service.correct("alpha:3", "amount", "800", "frais corrigés")
    assert service.verify_journal().valid
    with service.store.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_log SET new_value = '9999' WHERE id = 1")
    check = service.verify_journal()
    assert not check.valid and check.first_broken_entry == 1
