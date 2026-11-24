"""API HTTP locale de SIDADR (FastAPI).

L'API n'ajoute aucune logique métier : elle expose le moteur existant (import, analyse,
constats, corrections, journal, exports) à l'interface web. Elle écoute uniquement sur
127.0.0.1 par défaut et n'autorise que l'origine du frontend de développement.
"""

from __future__ import annotations

import io
import re
import shutil
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from sidadr import __version__
from sidadr.analysis import (
    analyze,
    findings_to_records,
    load_findings,
    load_summary,
    save_findings,
    save_summary,
    summarize,
)
from sidadr.config import ConfigError, default_schema, default_settings, schema_to_dict
from sidadr.correction import EXCLUDED, CorrectionError, CorrectionService, Decision
from sidadr.export import export_session
from sidadr.findings import Severity
from sidadr.ingestion import ImportRejected, ImportSession, MappingError
from sidadr.ingestion.generic import infer_generic_schema, suggest_mode
from sidadr.ingestion.readers import RawTable
from sidadr.reporting import build_traceability
from sidadr.reporting.html_report import render_report
from sidadr.storage import Workspace, WorkspaceError

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


# --------------------------------------------------------------------------- schémas


class SessionCreate(BaseModel):
    upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str | None = Field(default=None, max_length=200)
    # Corrections de correspondance : {fichier: {colonne: champ ou null}}. La création vaut confirmation.
    mappings: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    # auto : mode bancaire si chaque fichier couvre les champs bancaires obligatoires, sinon générique.
    mode: Literal["auto", "banking", "generic"] = "auto"
    key_fields: list[str] = Field(default_factory=list, max_length=10)
    required_fields: list[str] = Field(default_factory=list, max_length=200)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    # Types imposés en mode générique : {colonne: "text" | "decimal" | "date"}.
    types: dict[str, Literal["text", "decimal", "date"]] = Field(
        default_factory=dict, max_length=200
    )


class DecisionIn(BaseModel):
    decision: Decision
    reason: str = Field(min_length=1, max_length=2000)


class CorrectionIn(BaseModel):
    record_id: str = Field(max_length=200)
    field: str = Field(max_length=100)
    value: str | None = Field(default=None, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)


class ExclusionIn(BaseModel):
    record_id: str = Field(max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class UndoIn(BaseModel):
    target: str = Field(max_length=20)
    reason: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------- application


def create_app(workspace_root: Path, allowed_origins: list[str] | None = None) -> FastAPI:
    workspace = Workspace(workspace_root)
    uploads = workspace_root / "uploads"
    settings = default_settings()
    app = FastAPI(
        title="SIDADR API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Sidadr-Actor"],
    )

    # ---- erreurs : messages lisibles, jamais de trace technique

    @app.exception_handler(CorrectionError)
    async def _correction(_: Request, exc: CorrectionError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "invalid_action"}, status_code=422)

    @app.exception_handler(ImportRejected)
    async def _rejected(_: Request, exc: ImportRejected) -> JSONResponse:
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=422)

    @app.exception_handler(MappingError)
    async def _mapping(_: Request, exc: MappingError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "mapping"}, status_code=422)

    @app.exception_handler(WorkspaceError)
    async def _workspace(_: Request, exc: WorkspaceError) -> JSONResponse:
        status = 404 if "introuvable" in str(exc) or "invalide" in str(exc) else 409
        return JSONResponse({"detail": str(exc), "code": "session"}, status_code=status)

    @app.exception_handler(ConfigError)
    async def _config(_: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "configuration"}, status_code=500)

    def service(session_id: str, actor: str | None = None) -> CorrectionService:
        return CorrectionService(workspace.open_session(session_id), actor=actor or None)

    def state(svc: CorrectionService) -> dict[str, Any]:
        summary = load_summary(svc.store.summary_path)
        trace = build_traceability(svc, summary)
        return {
            **svc.store.metadata,
            # schéma effectif, y compris pour les sessions antérieures au mode générique
            "schema": schema_to_dict(svc.schema),
            "working_rows": trace["working_rows"],
            "excluded_records": trace["excluded_records"],
            "active_corrections": trace["active_corrections"],
            "active_decisions": trace["active_decisions"],
            "journal_entries": trace["journal_entries"],
            "journal_integrity": trace["journal_integrity"],
            "analysis": summary,
            "analysis_up_to_date": trace["analysis_up_to_date"],
        }

    def findings_with_status(svc: CorrectionService) -> list[dict[str, Any]]:
        decisions = svc.decisions()
        records = load_findings(svc.store.findings_path)
        for r in records:
            r["status"] = decisions.get(r["key"], "open")
        return records

    def frame_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
        clean = frame.astype(object).where(frame.notna(), None)
        return [{str(k): v for k, v in row.items()} for row in clean.to_dict(orient="records")]

    # ---- système

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/schema")
    def schema() -> list[dict[str, Any]]:
        return [
            {
                "name": f.name,
                "label": f.label,
                "type": f.type,
                "required": f.required,
                "values": list(f.values),
            }
            for f in default_schema().fields
        ]

    # ---- import en deux temps : téléversement + inspection, puis création confirmée

    @app.post("/api/imports")
    async def upload(files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
        if not files or len(files) > 20:
            raise HTTPException(422, "Entre 1 et 20 fichiers par import.")
        upload_id = uuid.uuid4().hex
        target = uploads / upload_id
        target.mkdir(parents=True)
        limit = int(settings.imports.max_file_mb * 1024 * 1024)
        session = ImportSession()
        inspections: list[dict[str, Any]] = []
        raws: list[RawTable] = []
        missing: dict[str, list[str]] = {}
        try:
            for item in files:
                name = _SAFE_NAME.sub("_", Path(item.filename or "fichier").name)[:120] or "fichier"
                data = await item.read(limit + 1)
                if len(data) > limit:
                    raise ImportRejected(
                        "too_large", f"{name} dépasse {settings.imports.max_file_mb:g} Mo."
                    )
                path = target / name
                path.write_bytes(data)
                inspection = session.inspect(path)
                raw = inspection.raw
                raws.append(raw)
                missing[raw.source_file] = inspection.mapping.missing_required(session.schema)
                inspections.append(
                    {
                        "file": raw.source_file,
                        "kind": raw.kind,
                        "encoding": raw.encoding,
                        "delimiter": raw.delimiter,
                        "sheet": raw.sheet,
                        "rows": raw.n_rows,
                        "rejected_rows": [
                            {"row": r.source_row, "reason": r.reason} for r in raw.rejected[:20]
                        ],
                        "rejected_count": len(raw.rejected),
                        "empty_columns": inspection.empty_columns,
                        "warnings": raw.warnings,
                        "headers": raw.headers,
                        "preview": raw.rows[:8],
                        "proposals": [
                            {
                                "source": p.source,
                                "target": p.target,
                                "confidence": p.confidence,
                                "method": p.method.value,
                                "note": p.note,
                            }
                            for p in inspection.mapping.proposals
                        ],
                        "missing_required": inspection.mapping.missing_required(session.schema),
                    }
                )
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        generic = infer_generic_schema(raws)
        return {
            "upload_id": upload_id,
            "files": inspections,
            "suggested_mode": suggest_mode(missing),
            "generic_fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "type": f.type,
                    "filled": f.filled,
                    "conformity": round(f.conformity, 3),
                    "sources": list(f.sources),
                    "reason": f.reason,
                }
                for f in generic.fields
            ],
        }

    @app.post("/api/sessions", status_code=201)
    def create_session(payload: SessionCreate) -> dict[str, Any]:
        source = uploads / payload.upload_id
        if not source.is_dir():
            raise HTTPException(404, "Téléversement introuvable ou expiré : recommencez l'import.")
        probe = ImportSession()
        inspections = [probe.inspect(p) for p in sorted(p for p in source.iterdir() if p.is_file())]
        missing = {i.source_file: i.mapping.missing_required(probe.schema) for i in inspections}
        mode = suggest_mode(missing) if payload.mode == "auto" else payload.mode
        if mode == "banking":
            for inspection in inspections:
                probe.add(
                    inspection,
                    inspection.mapping.confirm(
                        probe.schema, payload.mappings.get(inspection.source_file)
                    ),
                )
            store = workspace.create_session(probe, payload.name, "banking")
        else:
            generic = infer_generic_schema([i.raw for i in inspections]).with_options(
                payload.required_fields, payload.types
            )
            session = ImportSession(schema=generic.schema)
            for inspection in inspections:
                session.add(
                    inspection, generic.mappings[inspection.source_file].confirm(generic.schema)
                )
            options = {
                "key_fields": [generic.resolve(k) for k in payload.key_fields],
                "group_by": [generic.resolve(g) for g in payload.group_by],
            }
            store = workspace.create_session(session, payload.name, "generic", options)
        shutil.rmtree(source, ignore_errors=True)
        return state(CorrectionService(store))

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        return workspace.list_sessions()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        return state(service(session_id))

    # ---- analyse et constats

    @app.post("/api/sessions/{session_id}/analyze")
    def run_analysis(session_id: str) -> dict[str, Any]:
        svc = service(session_id)
        result = analyze(
            svc.working_frame(), svc.schema, mode=svc.store.mode, options=svc.store.options
        )
        records = findings_to_records(result.findings(), svc.decisions())
        save_findings(svc.store.findings_path, records)
        summary = summarize(result, records, svc.data_changes())
        save_summary(svc.store.summary_path, summary)
        return summary

    @app.get("/api/sessions/{session_id}/findings")
    def list_findings(
        session_id: str,
        severity: Severity | None = None,
        kind: Annotated[
            str | None,
            Query(max_length=300, description="un ou plusieurs types séparés par des virgules"),
        ] = None,
        status: str | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        records = findings_with_status(service(session_id))
        needle = (q or "").strip().lower()
        kinds = {k.strip() for k in (kind or "").split(",") if k.strip()}
        tests: dict[str, Callable[[dict[str, Any]], bool]] = {
            "severity": lambda r: severity is None or r["severity"] == severity.value,
            "kind": lambda r: not kinds or r["kind"] in kinds,
            "status": lambda r: status is None or r["status"] == status,
            "q": lambda r: not needle or needle in r["explanation"].lower() or needle in r["finding_id"].lower()
            or any(needle in rid.lower() for rid in r["record_ids"]),
        }  # fmt: skip

        def matches(r: dict[str, Any], ignore: str | None = None) -> bool:
            return all(test(r) for name, test in tests.items() if name != ignore)

        selected = [r for r in records if matches(r)]
        selected.sort(key=lambda r: (Severity(r["severity"]).rank, r["finding_id"]))
        start = (page - 1) * page_size

        def facet(key: str) -> dict[str, int]:
            # Chaque facette compte selon les autres filtres actifs, pas le sien.
            counts: dict[str, int] = {}
            for r in records:
                if matches(r, ignore=key):
                    counts[r[key]] = counts.get(r[key], 0) + 1
            return counts

        return {
            "total": len(selected),
            "page": page,
            "page_size": page_size,
            "items": selected[start : start + page_size],
            "facets": {
                "severity": facet("severity"),
                "kind": facet("kind"),
                "status": facet("status"),
            },
        }

    @app.get("/api/sessions/{session_id}/findings/{finding_id}")
    def get_finding(session_id: str, finding_id: str) -> dict[str, Any]:
        svc = service(session_id)
        finding = next(
            (r for r in findings_with_status(svc) if r["finding_id"] == finding_id), None
        )
        if finding is None:
            raise HTTPException(404, f"Constat {finding_id} introuvable dans la dernière analyse.")
        frame = svc.working_frame(include_excluded=True)
        excluded = svc.excluded_records()
        rows = frame[frame["record_id"].isin(finding["record_ids"][:100])]
        records = frame_rows(rows)
        for r in records:
            r["excluded"] = r["record_id"] in excluded
        return {**finding, "records": records}

    @app.post("/api/sessions/{session_id}/findings/{finding_id}/decision", status_code=201)
    def decide(
        session_id: str,
        finding_id: str,
        payload: DecisionIn,
        x_sidadr_actor: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        svc = service(session_id, x_sidadr_actor)
        finding = next(
            (r for r in load_findings(svc.store.findings_path) if r["finding_id"] == finding_id),
            None,
        )
        if finding is None:
            raise HTTPException(404, f"Constat {finding_id} introuvable dans la dernière analyse.")
        decision_id = svc.decide(finding["key"], finding_id, payload.decision, payload.reason)
        return {
            "target": f"D{decision_id}",
            "finding_id": finding_id,
            "decision": payload.decision.value,
        }

    # ---- données, corrections et journal

    @app.get("/api/sessions/{session_id}/records")
    def list_records(
        session_id: str,
        q: Annotated[str | None, Query(max_length=100)] = None,
        include_excluded: bool = False,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        svc = service(session_id)
        frame = svc.working_frame(include_excluded=include_excluded)
        needle = (q or "").strip().lower()
        if needle:
            # Toutes les colonnes du schéma de la session : fonctionne aussi en mode générique.
            columns = ["record_id", *(n for n in svc.schema.names if n in frame.columns)]
            mask = pd.Series(False, index=frame.index)
            for column in columns:
                mask |= (
                    frame[column]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                    .str.contains(needle, regex=False)
                )
            frame = frame[mask]
        start = (page - 1) * page_size
        excluded = svc.excluded_records()
        corrected: dict[str, list[str]] = {}
        for row in svc.active_corrections():
            if row["field"] != EXCLUDED:
                corrected.setdefault(row["record_id"], []).append(row["field"])
        items = frame_rows(
            frame.drop(columns=[EXCLUDED], errors="ignore").iloc[start : start + page_size]
        )
        for item in items:
            item["excluded"] = item["record_id"] in excluded
            item["corrected_fields"] = sorted(set(corrected.get(item["record_id"], [])))
        return {"total": len(frame), "page": page, "page_size": page_size, "items": items}

    @app.get("/api/sessions/{session_id}/records/{record_id}")
    def get_record(session_id: str, record_id: str) -> dict[str, Any]:
        svc = service(session_id)
        original = svc.original_frame()
        match = original[original["record_id"] == record_id]
        if match.empty:
            raise HTTPException(404, f"Enregistrement inconnu : {record_id}")
        working = svc.working_frame(include_excluded=True)
        with svc.store.connect() as connection:
            corrections = [dict(r) for r in connection.execute(
                "SELECT * FROM corrections WHERE record_id = ? ORDER BY id", (record_id,)
            ).fetchall()]  # fmt: skip
        findings = [r for r in findings_with_status(svc) if record_id in r["record_ids"]]
        return {
            "record_id": record_id,
            "original": frame_rows(match)[0],
            "working": frame_rows(
                working.drop(columns=[EXCLUDED], errors="ignore")[working["record_id"] == record_id]
            )[0],
            "excluded": record_id in svc.excluded_records(),
            "corrections": corrections,
            "findings": findings,
        }

    @app.post("/api/sessions/{session_id}/corrections", status_code=201)
    def correct(
        session_id: str,
        payload: CorrectionIn,
        x_sidadr_actor: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        svc = service(session_id, x_sidadr_actor)
        before = svc.current_value(payload.record_id, payload.field)
        correction_id = svc.correct(payload.record_id, payload.field, payload.value, payload.reason)
        return {"target": f"C{correction_id}", "record_id": payload.record_id, "field": payload.field,
                "old_value": before, "new_value": svc.current_value(payload.record_id, payload.field)}  # fmt: skip

    @app.post("/api/sessions/{session_id}/exclusions", status_code=201)
    def exclude(
        session_id: str,
        payload: ExclusionIn,
        x_sidadr_actor: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        svc = service(session_id, x_sidadr_actor)
        return {
            "target": f"C{svc.exclude(payload.record_id, payload.reason)}",
            "record_id": payload.record_id,
        }

    @app.post("/api/sessions/{session_id}/undo")
    def undo(
        session_id: str, payload: UndoIn, x_sidadr_actor: Annotated[str | None, Header()] = None
    ) -> dict[str, str]:
        service(session_id, x_sidadr_actor).undo(payload.target, payload.reason)
        return {"target": payload.target.upper(), "status": "undone"}

    @app.get("/api/sessions/{session_id}/history")
    def history(session_id: str) -> dict[str, Any]:
        svc = service(session_id)
        check = svc.verify_journal()
        return {"entries": jsonable_encoder(svc.history()), "integrity": jsonable_encoder(check)}

    # ---- sorties

    @app.get("/api/sessions/{session_id}/export")
    def export(
        session_id: str, format: Annotated[str, Query(pattern="^(xlsx|csv)$")] = "xlsx"
    ) -> Response:
        svc = service(session_id)
        out = workspace_root / "exports" / session_id / uuid.uuid4().hex
        result = export_session(svc, out, format)
        if format == "xlsx":
            (path,) = result.files
            content, media = (
                path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            filename = path.name
        else:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in result.files:
                    archive.write(path, path.name)
            content, media, filename = (
                buffer.getvalue(),
                "application/zip",
                f"sidadr_{session_id}_csv.zip",
            )
        shutil.rmtree(out, ignore_errors=True)
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/sessions/{session_id}/report", response_class=HTMLResponse)
    def report(session_id: str) -> HTMLResponse:
        try:
            html = render_report(service(session_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return HTMLResponse(
            html,
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    return app
