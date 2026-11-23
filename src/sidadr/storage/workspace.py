"""Espace de travail local : une session = un instantané immuable + un journal SQLite.

    workspace/
      sessions/<session_id>/
        original.parquet   données importées, jamais modifiées (lecture seule + empreinte SHA-256)
        session.json       métadonnées (fichiers, correspondances, empreinte)
        journal.sqlite     corrections, décisions et journal d'audit
        findings.json      constats de la dernière analyse

L'empreinte de l'instantané est vérifiée à chaque ouverture : une modification de
l'original, accidentelle ou non, est détectée et bloque la session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from sidadr.config import Schema, default_schema, schema_from_dict, schema_to_dict
from sidadr.ingestion import ImportSession

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    finding_key TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'kept_both', 'deferred')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT,
    record_id TEXT,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    finding_id TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
-- Le journal d'audit est en ajout seul : toute modification ou suppression est refusée.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'Le journal d''audit est en ajout seul.'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'Le journal d''audit est en ajout seul.'); END;
-- Une correction ou une décision n'est jamais supprimée ; seule son activité change.
CREATE TRIGGER IF NOT EXISTS corrections_no_delete BEFORE DELETE ON corrections
BEGIN SELECT RAISE(ABORT, 'Une correction ne peut pas être supprimée, seulement annulée.'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'Une décision ne peut pas être supprimée, seulement annulée.'); END;
"""


class WorkspaceError(Exception):
    """Session introuvable, corrompue ou opération refusée."""


def default_workspace() -> Path:
    return Path(os.environ.get("SIDADR_WORKSPACE", "workspace"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class SessionStore:
    session_id: str
    directory: Path
    metadata: dict[str, Any]

    @property
    def original_path(self) -> Path:
        return self.directory / "original.parquet"

    @property
    def findings_path(self) -> Path:
        return self.directory / "findings.json"

    @property
    def summary_path(self) -> Path:
        return self.directory / "analysis_summary.json"

    @property
    def mode(self) -> str:
        """« banking » (schéma bancaire) ou « generic » (schéma déduit des fichiers)."""
        return str(self.metadata.get("mode", "banking"))

    @property
    def options(self) -> dict[str, Any]:
        """Options d'analyse : clé d'unicité, regroupement des anomalies, règles."""
        return dict(self.metadata.get("options") or {})

    def schema(self) -> Schema:
        """Schéma de la session : celui enregistré à la création, sinon le schéma bancaire."""
        raw = self.metadata.get("schema")
        return schema_from_dict(raw) if raw else default_schema()

    def original_frame(self) -> pd.DataFrame:
        """Relit l'instantané après avoir vérifié qu'il n'a pas été modifié."""
        if _sha256(self.original_path) != self.metadata["original_sha256"]:
            raise WorkspaceError(
                f"L'instantané original de la session {self.session_id} a été modifié : "
                "empreinte SHA-256 différente. La session est bloquée."
            )
        frame = pd.read_parquet(self.original_path)
        return frame.astype(object).where(frame.notna(), None)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.directory / "journal.sqlite")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


@dataclass
class Workspace:
    root: Path

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def create_session(
        self,
        session: ImportSession,
        name: str | None = None,
        mode: str = "banking",
        options: dict[str, Any] | None = None,
    ) -> SessionStore:
        if mode not in ("banking", "generic"):
            raise WorkspaceError(f"Mode inconnu : {mode!r} (attendus : banking, generic).")
        if not session.tables:
            raise WorkspaceError("Aucun fichier importé : rien à enregistrer.")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        session_id = f"S-{stamp}-{uuid.uuid4().hex[:6]}"
        directory = self.sessions_dir / session_id
        directory.mkdir(parents=True, exist_ok=False)

        frame = session.frame
        original = directory / "original.parquet"
        frame.astype("string").to_parquet(original, index=False)
        original.chmod(stat.S_IREAD)  # lecture seule : l'original ne se modifie pas

        metadata: dict[str, Any] = {
            "session_id": session_id,
            "name": name or ", ".join(t.report.source_file for t in session.tables),
            "created_at": _now(),
            "rows": len(frame),
            "original_sha256": _sha256(original),
            "mode": mode,
            "schema": schema_to_dict(session.schema),
            "options": options or {},
            "imports": [
                {
                    "import_id": t.import_id,
                    "source_file": t.report.source_file,
                    "rows": t.report.rows_imported,
                    "rows_rejected": len(t.report.rows_rejected),
                    "mapping": {p.source: p.target for p in t.mapping.proposals},
                }
                for t in session.tables
            ],
        }
        (directory / "session.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store = SessionStore(session_id, directory, metadata)
        with store.connect() as connection:
            connection.executescript(SCHEMA_SQL)
        return store

    def open_session(self, session_id: str) -> SessionStore:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise WorkspaceError(f"Identifiant de session invalide : {session_id!r}")
        directory = self.sessions_dir / session_id
        meta_path = directory / "session.json"
        if not meta_path.exists():
            raise WorkspaceError(f"Session introuvable : {session_id}")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        store = SessionStore(session_id, directory, metadata)
        with store.connect() as connection:
            connection.executescript(SCHEMA_SQL)
        return store

    def list_sessions(self) -> list[dict[str, Any]]:
        if not self.sessions_dir.exists():
            return []
        sessions = []
        for meta_path in sorted(self.sessions_dir.glob("*/session.json")):
            sessions.append(json.loads(meta_path.read_text(encoding="utf-8")))
        return sessions
