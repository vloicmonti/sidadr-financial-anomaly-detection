"""Parcours complet en ligne de commande : session, analyse, décision, correction, journal."""

import json
from pathlib import Path

import pytest

from sidadr.cli import main
from sidadr.synthetic import GeneratorConfig, generate, write_dataset

FILES = ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data")
    write_dataset(generate(GeneratorConfig(rows=1500, seed=7)), out)
    return out


@pytest.fixture(scope="module")
def session(data: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    workspace = tmp_path_factory.mktemp("ws")
    files = [str(data / name) for name in FILES]
    assert (
        main(["session-create", *files, "--workspace", str(workspace)]) == 2
    )  # confirmation exigée
    assert main(["session-create", *files, "--accept-proposed", "--workspace", str(workspace)]) == 0
    (session_id,) = [p.name for p in (workspace / "sessions").iterdir()]
    assert main(["analyze", session_id, "--workspace", str(workspace)]) == 0
    return workspace, session_id


def _findings(workspace: Path, session_id: str) -> list[dict[str, object]]:
    path = workspace / "sessions" / session_id / "findings.json"
    data: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_full_review_cycle(session: tuple[Path, str]) -> None:
    workspace, session_id = session
    ws = ["--workspace", str(workspace), "--actor", "auditeur"]
    findings = _findings(workspace, session_id)
    assert findings and all(f["status"] == "open" for f in findings)

    name_match = next(f for f in findings if f["kind"] == "name_match")
    assert (
        main(
            [
                "decide",
                session_id,
                str(name_match["finding_id"]),
                "rejected",
                "--reason",
                "homonymes vérifiés",
                *ws,
            ]
        )
        == 0
    )

    copy = next(f for f in findings if f["kind"] == "exact_duplicate_row")
    record = str(copy["record_ids"][1])  # type: ignore[index]
    assert main(["exclude", session_id, record, "--reason", "copie exportée deux fois", *ws]) == 0

    missing = next(
        f for f in findings if f["kind"] == "rule_violation" and f["field"] == "branch_code"
    )
    target = str(missing["record_ids"][0])  # type: ignore[index]
    assert (
        main(
            [
                "correct",
                session_id,
                target,
                "branch_code",
                "BALP-999",
                "--reason",
                "trop court",
                *ws,
            ]
        )
        == 0
    )

    assert main(["analyze", session_id, *ws]) == 0
    after = _findings(workspace, session_id)
    by_key = {f["key"]: f for f in after}
    assert (
        by_key[name_match["key"]]["status"] == "rejected"
    )  # la décision survit à la renumérotation
    assert not any(
        copy["key"] == f["key"] for f in after
    )  # le doublon a disparu de la vue de travail
    assert any(target in f["record_ids"] and f["rule_id"] == "R-BRANCH-REF" for f in after)  # type: ignore[operator]

    assert main(["history", session_id, "--verify", *ws]) == 0
    assert main(["findings", session_id, "--status", "rejected", *ws]) == 0


def test_invalid_actions_fail_cleanly(session: tuple[Path, str]) -> None:
    workspace, session_id = session
    ws = ["--workspace", str(workspace)]
    assert (
        main(["correct", session_id, "inconnu:1", "amount", "10", "--reason", "motif valable", *ws])
        == 1
    )
    assert main(["undo", session_id, "C999", "--reason", "motif valable", *ws]) == 1
    assert (
        main(["decide", session_id, "DUP-99999", "rejected", "--reason", "motif valable", *ws]) == 1
    )
    assert main(["analyze", "S-absente", *ws]) == 1
    assert main(["sessions", *ws]) == 0


def test_exports_and_report(session: tuple[Path, str], tmp_path: Path) -> None:
    workspace, session_id = session
    ws = ["--workspace", str(workspace)]
    assert main(["export", session_id, "--format", "xlsx", "--out", str(tmp_path), *ws]) == 0
    assert main(["export", session_id, "--format", "csv", "--out", str(tmp_path), *ws]) == 0
    report = tmp_path / "rapport.html"
    assert main(["report", session_id, "--out", str(report), *ws]) == 0
    assert (tmp_path / f"sidadr_{session_id}.xlsx").exists()
    assert (tmp_path / f"sidadr_{session_id}.meta.json").exists()
    assert "Score qualité" in report.read_text(encoding="utf-8")


def test_session_create_switches_to_generic_mode_automatically(tmp_path: Path) -> None:
    path = tmp_path / "ventes.csv"
    path.write_text(
        "Ref;Client;Montant\n"
        + "".join(f"V{i};C{i % 9};{100 + i}\n" for i in range(50))
        + "V1;C1;101\n",
        encoding="utf-8",
    )
    workspace = ["--workspace", str(tmp_path / "ws")]
    assert (
        main(["session-create", str(path), *workspace]) == 2
    )  # le schéma déduit doit être confirmé
    assert main(["session-create", str(path), "--accept-proposed", "--key", "Ref", *workspace]) == 0
    (session_id,) = [p.name for p in (tmp_path / "ws" / "sessions").iterdir()]
    assert main(["analyze", session_id, *workspace]) == 0
    findings = json.loads(
        (tmp_path / "ws" / "sessions" / session_id / "findings.json").read_text(encoding="utf-8")
    )
    assert [f["kind"] for f in findings] == ["exact_duplicate_row"]
    assert (
        main(["session-create", str(path), "--accept-proposed", "--key", "Inconnue", *workspace])
        == 1
    )
