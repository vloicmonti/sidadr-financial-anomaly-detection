"""Parcours complet par l'API : import, création de session, analyse, revue, exports."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidadr.api import create_app
from sidadr.synthetic import GeneratorConfig, generate, write_dataset

FILES = ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")
ACTOR = {"X-Sidadr-Actor": "auditrice"}


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data")
    write_dataset(generate(GeneratorConfig(rows=1500, seed=11)), out)
    return out


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    return TestClient(create_app(tmp_path_factory.mktemp("ws")))


@pytest.fixture(scope="module")
def session_id(client: TestClient, data: Path) -> str:
    files = [("files", (name, (data / name).read_bytes())) for name in FILES]
    upload = client.post("/api/imports", files=files)
    assert upload.status_code == 200, upload.text
    body = upload.json()
    assert [f["file"] for f in body["files"]] == list(FILES)
    gamma = body["files"][2]
    assert gamma["encoding"] == "cp1252" and gamma["empty_columns"] == ["commentaire"]
    assert all(not f["missing_required"] for f in body["files"])
    assert len(gamma["preview"]) == 8

    created = client.post(
        "/api/sessions", json={"upload_id": body["upload_id"], "name": "Test API", "mappings": {}}
    )
    assert created.status_code == 201, created.text
    assert created.json()["analysis"] is None
    sid: str = created.json()["session_id"]
    analysis = client.post(f"/api/sessions/{sid}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["score"]["score"] > 0
    return sid


def _first(client: TestClient, sid: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/sessions/{sid}/findings", params={**params, "page_size": 1})
    assert response.status_code == 200
    item: dict[str, Any] = response.json()["items"][0]
    return item


def test_health_and_schema(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    names = [f["name"] for f in client.get("/api/schema").json()]
    assert "customer_name" in names and "amount" in names


def test_session_state(client: TestClient, session_id: str) -> None:
    state = client.get(f"/api/sessions/{session_id}").json()
    assert state["name"] == "Test API"
    assert state["analysis_up_to_date"] is True
    assert state["journal_integrity"] == "intact"
    assert session_id in [s["session_id"] for s in client.get("/api/sessions").json()]


def test_analysis_keeps_the_column_profile(client: TestClient, session_id: str) -> None:
    profile = client.get(f"/api/sessions/{session_id}").json()["analysis"]["profile"]
    columns = {c["name"]: c for c in profile["columns"]}
    amount, txn_date = columns["amount"], columns["transaction_date"]
    assert profile["rows"] == amount["rows"] > 1000
    assert amount["numeric"] is not None and float(amount["numeric"]["min"]) <= float(
        amount["numeric"]["max"]
    )
    assert txn_date["date_range"] is not None and txn_date["checked_for_validity"] is True
    assert 0 < len(columns["bank_code"]["top_values"]) <= 5
    assert all(len(c["invalid_samples"]) <= 20 for c in profile["columns"])


def test_findings_filters_facets_and_pagination(client: TestClient, session_id: str) -> None:
    all_ = client.get(f"/api/sessions/{session_id}/findings", params={"page_size": 10}).json()
    assert all_["total"] > 10 and len(all_["items"]) == 10
    assert set(all_["facets"]) == {"severity", "kind", "status"}
    certain = client.get(
        f"/api/sessions/{session_id}/findings", params={"severity": "certain_error"}
    ).json()
    assert certain["total"] == all_["facets"]["severity"]["certain_error"]
    assert all(i["severity"] == "certain_error" for i in certain["items"])
    searched = client.get(f"/api/sessions/{session_id}/findings", params={"q": "RULE-00001"}).json()
    assert searched["items"][0]["finding_id"] == "RULE-00001"


def test_finding_detail_returns_the_records(client: TestClient, session_id: str) -> None:
    match = _first(client, session_id, kind="name_match")
    detail = client.get(f"/api/sessions/{session_id}/findings/{match['finding_id']}").json()
    assert {r["record_id"] for r in detail["records"]} <= set(match["record_ids"])
    assert all("excluded" in r for r in detail["records"])


def test_review_cycle(client: TestClient, session_id: str) -> None:
    match = _first(client, session_id, kind="name_match")
    decided = client.post(
        f"/api/sessions/{session_id}/findings/{match['finding_id']}/decision",
        json={"decision": "rejected", "reason": "homonymes vérifiés"},
        headers=ACTOR,
    )
    assert decided.status_code == 201
    rejected = client.get(
        f"/api/sessions/{session_id}/findings", params={"status": "rejected"}
    ).json()
    assert match["finding_id"] in [i["finding_id"] for i in rejected["items"]]
    # Une décision ne modifie pas les données : l'analyse reste à jour.
    assert client.get(f"/api/sessions/{session_id}").json()["analysis_up_to_date"] is True

    missing = _first(client, session_id, kind="rule_violation", q="branch_code = (vide)")
    record_id = missing["record_ids"][0]
    corrected = client.post(
        f"/api/sessions/{session_id}/corrections",
        json={
            "record_id": record_id,
            "field": "branch_code",
            "value": "BALP-001",
            "reason": "retrouvé à la source",
        },
        headers=ACTOR,
    )
    assert corrected.status_code == 201
    assert corrected.json()["old_value"] is None and corrected.json()["new_value"] == "BALP-001"
    record = client.get(f"/api/sessions/{session_id}/records/{record_id}").json()
    assert (
        record["original"]["branch_code"] is None and record["working"]["branch_code"] == "BALP-001"
    )
    assert len(record["corrections"]) == 1

    undone = client.post(
        f"/api/sessions/{session_id}/undo",
        json={"target": corrected.json()["target"], "reason": "annulation de test"},
    )
    assert undone.status_code == 200
    history = client.get(f"/api/sessions/{session_id}/history").json()
    assert [e["action_type"] for e in history["entries"]] == ["decide", "correct", "undo"]
    assert history["entries"][0]["actor"] == "auditrice"
    assert history["integrity"]["valid"] is True
    assert client.get(f"/api/sessions/{session_id}").json()["analysis_up_to_date"] is False


def test_records_search_and_pagination(client: TestClient, session_id: str) -> None:
    page = client.get(f"/api/sessions/{session_id}/records", params={"page_size": 5}).json()
    assert len(page["items"]) == 5 and page["total"] > 1000
    found = client.get(
        f"/api/sessions/{session_id}/records", params={"q": page["items"][0]["transaction_id"]}
    ).json()
    assert found["total"] >= 1


def test_exports_and_report(client: TestClient, session_id: str) -> None:
    xlsx = client.get(f"/api/sessions/{session_id}/export", params={"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert "attachment" in xlsx.headers["content-disposition"]
    csv = client.get(f"/api/sessions/{session_id}/export", params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"] == "application/zip"
    report = client.get(f"/api/sessions/{session_id}/report")
    assert report.status_code == 200 and "Score qualité" in report.text
    assert "default-src 'none'" in report.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("method", "path", "payload", "status"),
    [
        ("get", "/api/sessions/S-absente", None, 404),
        ("get", "/api/sessions/..%2F..%2Fetc", None, 404),
        ("post", "/api/sessions", {"upload_id": "0" * 32}, 404),
        ("post", "/api/sessions", {"upload_id": "pas-un-id"}, 422),
    ],
)
def test_errors_are_clean(
    client: TestClient, method: str, path: str, payload: dict[str, Any] | None, status: int
) -> None:
    response = client.request(method.upper(), path, json=payload)
    assert response.status_code == status
    assert "detail" in response.json()


def test_invalid_actions_return_422(client: TestClient, session_id: str) -> None:
    bad = client.post(
        f"/api/sessions/{session_id}/corrections",
        json={
            "record_id": "alpha_t2:1",
            "field": "amount",
            "value": "abc",
            "reason": "motif valable",
        },
    )
    assert bad.status_code == 422 and "nombre" in bad.json()["detail"]
    short = client.post(
        f"/api/sessions/{session_id}/exclusions", json={"record_id": "alpha_t2:1", "reason": "ok"}
    )
    assert short.status_code == 422


def test_uploads_reject_bad_files(client: TestClient) -> None:
    response = client.post("/api/imports", files=[("files", ("x.csv", b"\x00\x01binaire"))])
    assert response.status_code == 422 and response.json()["code"] == "binary_csv"
    response = client.post("/api/imports", files=[("files", ("../../evil.exe", b"MZ"))])
    assert response.status_code == 422 and response.json()["code"] == "extension"


def test_cors_allows_only_the_frontend(client: TestClient) -> None:
    allowed = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.options(
        "/api/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in other.headers


def test_generic_file_through_the_api(client: TestClient) -> None:
    csv = "Code article;Libellé;Quantité;Prix unitaire\n" + "".join(
        f"A{i:03d};Article {i};{i % 7 + 1};{10 + i % 5},90\n" for i in range(80)
    )
    csv += "A001;Doublon;3;12,90\nA090;;2;9990,00\n"
    upload = client.post("/api/imports", files=[("files", ("stock.csv", csv.encode("utf-8")))])
    assert upload.status_code == 200
    body = upload.json()
    assert body["suggested_mode"] == "generic"
    types = {f["label"]: f["type"] for f in body["generic_fields"]}
    assert types == {
        "Code article": "text",
        "Libellé": "text",
        "Quantité": "decimal",
        "Prix unitaire": "decimal",
    }

    created = client.post(
        "/api/sessions",
        json={
            "upload_id": body["upload_id"],
            "name": "Stock",
            "key_fields": ["Code article"],
            "required_fields": ["Libellé"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["mode"] == "generic"
    sid = created.json()["session_id"]
    summary = client.post(f"/api/sessions/{sid}/analyze").json()
    assert summary["mode"] == "generic"
    assert summary["findings"]["by_kind"].get("duplicate_key") == 1
    assert summary["findings"]["by_kind"].get("rule_violation") == 1
    bad = client.post(
        f"/api/sessions/{sid}/corrections",
        json={
            "record_id": "stock:1",
            "field": "quantite",
            "value": "beaucoup",
            "reason": "test de type",
        },
    )
    assert bad.status_code == 422
    # La recherche d'enregistrements porte sur les colonnes du schéma générique.
    found = client.get(f"/api/sessions/{sid}/records", params={"q": "doublon"}).json()
    assert found["total"] == 1 and found["items"][0]["libelle"] == "Doublon"


def test_unknown_generic_key_is_rejected(client: TestClient) -> None:
    upload = client.post("/api/imports", files=[("files", ("x.csv", b"A;B\n1;2\n3;4\n"))]).json()
    response = client.post(
        "/api/sessions", json={"upload_id": upload["upload_id"], "key_fields": ["Inexistante"]}
    )
    assert response.status_code == 422 and "Colonne inconnue" in response.json()["detail"]


def test_findings_can_be_filtered_by_several_kinds(client: TestClient, session_id: str) -> None:
    both = client.get(
        f"/api/sessions/{session_id}/findings",
        params={"kind": "exact_duplicate_row,duplicate_key", "page_size": 200},
    ).json()
    facets = both["facets"]["kind"]
    assert both["total"] == facets.get("exact_duplicate_row", 0) + facets.get("duplicate_key", 0)
    assert {i["kind"] for i in both["items"]} <= {"exact_duplicate_row", "duplicate_key"}


def test_facets_follow_the_other_active_filters(client: TestClient, session_id: str) -> None:
    rules = client.get(
        f"/api/sessions/{session_id}/findings", params={"kind": "rule_violation"}
    ).json()
    every = client.get(f"/api/sessions/{session_id}/findings").json()
    # La facette de sévérité est restreinte au type choisi ; celle du type ignore son propre filtre.
    assert sum(rules["facets"]["severity"].values()) == rules["total"]
    assert rules["facets"]["kind"] == every["facets"]["kind"]
    assert sum(every["facets"]["severity"].values()) == every["total"]


def test_session_state_always_exposes_the_effective_schema(
    client: TestClient, session_id: str
) -> None:
    state = client.get(f"/api/sessions/{session_id}").json()
    names = [f["name"] for f in state["schema"]]
    assert "transaction_id" in names and "amount" in names


def test_records_flag_corrections_and_exclusions(client: TestClient) -> None:
    csv = b"Code;Libelle;Prix\nA1;Vis;1,50\nA2;Ecrou;0,90\nA3;Clou;0,10\n"
    upload = client.post("/api/imports", files=[("files", ("pieces.csv", csv))]).json()
    sid = client.post("/api/sessions", json={"upload_id": upload["upload_id"]}).json()["session_id"]
    first, second = (
        i["record_id"] for i in client.get(f"/api/sessions/{sid}/records").json()["items"][:2]
    )
    reason = {"reason": "contrôle fournisseur"}
    client.post(
        f"/api/sessions/{sid}/corrections",
        json={"record_id": first, "field": "prix", "value": "1.60", **reason},
    )
    excluded = client.post(f"/api/sessions/{sid}/exclusions", json={"record_id": second, **reason})
    assert excluded.status_code == 201

    visible = client.get(f"/api/sessions/{sid}/records").json()
    assert visible["total"] == 2
    assert (
        visible["items"][0]["corrected_fields"] == ["prix"]
        and "__excluded__" not in visible["items"][0]
    )
    every = client.get(f"/api/sessions/{sid}/records", params={"include_excluded": True}).json()
    assert [i["excluded"] for i in every["items"]] == [False, True, False]

    detail = client.get(f"/api/sessions/{sid}/records/{second}").json()
    assert detail["excluded"] is True and "__excluded__" not in detail["working"]
    client.post(f"/api/sessions/{sid}/undo", json={"target": excluded.json()["target"], **reason})
    assert client.get(f"/api/sessions/{sid}/records").json()["total"] == 3


def test_types_can_be_chosen_when_creating_a_generic_session(client: TestClient) -> None:
    csv = b"Code postal;Quantite\n01000;3\n75001;beaucoup\n13001;5\n"
    upload = client.post("/api/imports", files=[("files", ("cp.csv", csv))]).json()
    assert {f["label"]: f["type"] for f in upload["generic_fields"]}["Quantite"] == "text"

    refused = client.post(
        "/api/sessions", json={"upload_id": upload["upload_id"], "types": {"Quantite": "enum"}}
    )
    assert refused.status_code == 422
    created = client.post(
        "/api/sessions", json={"upload_id": upload["upload_id"], "types": {"Quantite": "decimal"}}
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert {f["name"]: f["type"] for f in created.json()["schema"]} == {
        "code_postal": "text",
        "quantite": "decimal",
    }
    profile = client.post(f"/api/sessions/{sid}/analyze").json()["profile"]
    quantity = next(c for c in profile["columns"] if c["name"] == "quantite")
    assert (quantity["rows"], quantity["invalid"]) == (3, 1)


def test_calculation_checks_through_the_api(client: TestClient) -> None:
    csv = "Ref;Quantité;Prix unitaire;Total\nT1;2;2,00;4,00\nT2;3;1,50;9,00\nT3;;1,00;2,00\n"
    upload = client.post("/api/imports", files=[("files", ("v.csv", csv.encode()))]).json()
    check = {"result": "Total", "left": "Quantité", "operator": "*", "right": "Prix unitaire"}

    refused = client.post(
        "/api/sessions",
        json={"upload_id": upload["upload_id"], "checks": [{**check, "left": "Ref"}]},
    )
    assert refused.status_code == 422 and "pas un champ numérique" in refused.json()["detail"]

    created = client.post(
        "/api/sessions",
        json={"upload_id": upload["upload_id"], "checks": [{**check, "tolerance": "0.01"}]},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    summary = client.post(f"/api/sessions/{sid}/analyze").json()
    calc = next(r for r in summary["rules"] if r["rule_id"] == "R-CALC-1")
    assert (calc["violations"], calc["not_evaluable"]) == (1, 1)
    consistency = next(d for d in summary["score"]["dimensions"] if d["name"] == "consistency")
    assert consistency["value"] is not None
    finding = client.get(f"/api/sessions/{sid}/findings", params={"q": "calculé"}).json()["items"]
    assert len(finding) == 1 and finding[0]["severity"] == "probable_inconsistency"
