"""Module H — corrections, décisions et journal d'audit.

Principes :
- l'original n'est jamais modifié : la vue de travail = original + corrections actives ;
- toute action exige un motif et est inscrite dans un journal en ajout seul ;
- chaque entrée du journal est chaînée à la précédente par une empreinte SHA-256 :
  une modification a posteriori du fichier SQLite est détectable (`verify_journal`) ;
- toute correction, exclusion ou décision est réversible (`undo`), et l'annulation
  est elle-même journalisée.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import pandas as pd

from sidadr.config import Schema
from sidadr.ingestion.values import DateStatus, parse_date, parse_decimal
from sidadr.storage import SessionStore

EXCLUDED = "__excluded__"  # pseudo-champ : l'enregistrement est écarté de la vue de travail
MIN_REASON_LENGTH = 5
GENESIS_HASH = "0" * 64


class CorrectionError(ValueError):
    """Action refusée (motif absent, valeur invalide, cible inconnue...)."""


class Decision(StrEnum):
    CONFIRMED = "confirmed"  # le constat est fondé
    REJECTED = "rejected"  # faux positif
    KEPT_BOTH = "kept_both"  # correspondance réelle, mais les deux enregistrements sont conservés
    DEFERRED = "deferred"  # à revoir plus tard


@dataclass(frozen=True)
class AuditEntry:
    entry_id: int
    ts_utc: str
    actor: str
    action_type: str
    target: str | None
    record_id: str | None
    field: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None
    finding_id: str | None


@dataclass(frozen=True)
class JournalCheck:
    entries: int
    valid: bool
    first_broken_entry: int | None = None


def _hash(prev: str, payload: dict[str, object]) -> str:
    text = prev + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CorrectionService:
    def __init__(
        self,
        store: SessionStore,
        schema: Schema | None = None,
        actor: str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.schema = schema or store.schema()
        self.actor = actor or getpass.getuser()
        self.clock = clock or (lambda: datetime.now(UTC).isoformat(timespec="seconds"))
        self._original = store.original_frame()
        self._records = set(self._original["record_id"].astype(str))

    # ------------------------------------------------------------------ journal

    def _audit(
        self, connection: sqlite3.Connection, action_type: str, **fields: str | None
    ) -> None:
        row = connection.execute(
            "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = row["row_hash"] if row else GENESIS_HASH
        payload: dict[str, object] = {
            "ts_utc": self.clock(),
            "session_id": self.store.session_id,
            "actor": self.actor,
            "action_type": action_type,
            **{
                k: fields.get(k)
                for k in (
                    "target",
                    "record_id",
                    "field",
                    "old_value",
                    "new_value",
                    "reason",
                    "finding_id",
                )
            },
        }
        connection.execute(
            "INSERT INTO audit_log (ts_utc, session_id, actor, action_type, target, record_id, field, "
            "old_value, new_value, reason, finding_id, prev_hash, row_hash) "
            "VALUES (:ts_utc, :session_id, :actor, :action_type, :target, :record_id, :field, "
            ":old_value, :new_value, :reason, :finding_id, :prev_hash, :row_hash)",
            {**payload, "prev_hash": prev, "row_hash": _hash(prev, payload)},
        )

    def history(self) -> list[AuditEntry]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [
            AuditEntry(r["id"], r["ts_utc"], r["actor"], r["action_type"], r["target"], r["record_id"],
                       r["field"], r["old_value"], r["new_value"], r["reason"], r["finding_id"])
            for r in rows
        ]  # fmt: skip

    def verify_journal(self) -> JournalCheck:
        """Recalcule la chaîne d'empreintes : détecte toute entrée modifiée ou supprimée."""
        with self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        prev = GENESIS_HASH
        for row in rows:
            payload = {
                k: row[k]
                for k in ("ts_utc", "session_id", "actor", "action_type", "target", "record_id",
                          "field", "old_value", "new_value", "reason", "finding_id")
            }  # fmt: skip
            if row["prev_hash"] != prev or row["row_hash"] != _hash(prev, payload):
                return JournalCheck(len(rows), False, int(row["id"]))
            prev = row["row_hash"]
        return JournalCheck(len(rows), True)

    # ------------------------------------------------------------------ contrôles

    def _check_reason(self, reason: str) -> str:
        reason = (reason or "").strip()
        if len(reason) < MIN_REASON_LENGTH:
            raise CorrectionError(
                f"Un motif d'au moins {MIN_REASON_LENGTH} caractères est obligatoire."
            )
        return reason

    def _check_record(self, record_id: str) -> None:
        if record_id not in self._records:
            raise CorrectionError(f"Enregistrement inconnu dans cette session : {record_id}")

    def _check_value(self, field: str, value: str | None) -> None:
        if field not in self.schema.names:
            raise CorrectionError(f"Champ inconnu : {field}")
        spec = self.schema[field]
        if value is None or not value.strip():
            if spec.required:
                raise CorrectionError(f"{field} est obligatoire : il ne peut pas être vidé.")
            return
        if spec.type == "decimal" and parse_decimal(value) is None:
            raise CorrectionError(f"{field} attend un nombre : {value!r} refusé.")
        if spec.type == "date" and parse_date(value)[0] is not DateStatus.VALID:
            raise CorrectionError(
                f"{field} attend une date valide au format AAAA-MM-JJ : {value!r} refusé."
            )
        if spec.type == "enum" and value.strip() not in spec.values:
            raise CorrectionError(
                f"{field} attend l'une des valeurs {', '.join(spec.values)} : {value!r} refusé."
            )

    def current_value(self, record_id: str, field: str) -> str | None:
        self._check_record(record_id)
        if field not in self.schema.names:
            raise CorrectionError(f"Champ inconnu : {field}")
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT new_value FROM corrections WHERE record_id = ? AND field = ? AND active = 1 "
                "ORDER BY id DESC LIMIT 1",
                (record_id, field),
            ).fetchone()
        if row is not None:
            return row["new_value"]  # type: ignore[no-any-return]
        value = self._original.loc[self._original["record_id"] == record_id, field].iloc[0]
        return None if value is None else str(value)

    # ------------------------------------------------------------------ actions

    def correct(self, record_id: str, field: str, new_value: str | None, reason: str) -> int:
        """Corrige une valeur. Renvoie l'identifiant de la correction (pour l'annuler)."""
        reason = self._check_reason(reason)
        self._check_record(record_id)
        self._check_value(field, new_value)
        old = self.current_value(record_id, field)
        new = new_value.strip() if new_value and new_value.strip() else None
        if old == new:
            raise CorrectionError("La nouvelle valeur est identique à la valeur actuelle.")
        with self.store.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO corrections (record_id, field, old_value, new_value, reason, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record_id, field, old, new, reason, self.actor, self.clock()),
            )
            correction_id = int(cursor.lastrowid or 0)
            self._audit(connection, "correct", target=f"C{correction_id}", record_id=record_id,
                        field=field, old_value=old, new_value=new, reason=reason)  # fmt: skip
        return correction_id

    def exclude(self, record_id: str, reason: str) -> int:
        """Écarte un enregistrement de la vue de travail, sans le supprimer."""
        reason = self._check_reason(reason)
        self._check_record(record_id)
        if record_id in self.excluded_records():
            raise CorrectionError(f"{record_id} est déjà exclu.")
        with self.store.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO corrections (record_id, field, old_value, new_value, reason, actor, created_at) "
                "VALUES (?, ?, 'false', 'true', ?, ?, ?)",
                (record_id, EXCLUDED, reason, self.actor, self.clock()),
            )
            correction_id = int(cursor.lastrowid or 0)
            self._audit(
                connection,
                "exclude",
                target=f"C{correction_id}",
                record_id=record_id,
                reason=reason,
            )
        return correction_id

    def decide(self, finding_key: str, finding_id: str, decision: Decision, reason: str) -> int:
        """Enregistre une décision humaine sur un constat (la précédente est remplacée)."""
        reason = self._check_reason(reason)
        with self.store.connect() as connection:
            previous = connection.execute(
                "SELECT id, decision FROM decisions WHERE finding_key = ? AND active = 1",
                (finding_key,),
            ).fetchone()
            if previous is not None:
                connection.execute(
                    "UPDATE decisions SET active = 0 WHERE id = ?", (previous["id"],)
                )
            cursor = connection.execute(
                "INSERT INTO decisions (finding_key, finding_id, decision, reason, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (finding_key, finding_id, decision.value, reason, self.actor, self.clock()),
            )
            decision_id = int(cursor.lastrowid or 0)
            self._audit(connection, "decide", target=f"D{decision_id}", finding_id=finding_id,
                        old_value=previous["decision"] if previous else None, new_value=decision.value,
                        reason=reason)  # fmt: skip
        return decision_id

    def undo(self, target: str, reason: str) -> None:
        """Annule une correction (« C12 ») ou une décision (« D3 ») ; l'annulation est journalisée."""
        reason = self._check_reason(reason)
        target = target.strip().upper()
        tables = {"C": "corrections", "D": "decisions"}
        table, number = tables.get(target[:1]), target[1:]
        if table is None or not number.isdigit():
            raise CorrectionError(
                "Cible attendue : C<numéro> pour une correction, D<numéro> pour une décision."
            )
        with self.store.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?", (int(number),)
            ).fetchone()
            if row is None:
                raise CorrectionError(f"{target} introuvable.")
            if not row["active"]:
                raise CorrectionError(f"{target} est déjà annulé.")
            connection.execute(f"UPDATE {table} SET active = 0 WHERE id = ?", (int(number),))
            if table == "corrections":
                self._audit(connection, "undo", target=target, record_id=row["record_id"],
                            field=row["field"], old_value=row["new_value"], new_value=row["old_value"],
                            reason=reason)  # fmt: skip
            else:
                self._audit(connection, "undo", target=target, finding_id=row["finding_id"],
                            old_value=row["decision"], reason=reason)  # fmt: skip

    # ------------------------------------------------------------------ lecture

    def data_changes(self) -> int:
        """Entrées du journal qui modifient les données : tout sauf les décisions et leurs annulations."""
        return sum(
            1
            for e in self.history()
            if e.action_type != "decide"
            and not (e.action_type == "undo" and (e.target or "").startswith("D"))
        )

    def excluded_records(self) -> set[str]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT record_id FROM corrections WHERE field = ? AND active = 1", (EXCLUDED,)
            ).fetchall()
        return {r["record_id"] for r in rows}

    def active_corrections(self) -> list[sqlite3.Row]:
        with self.store.connect() as connection:
            return connection.execute(
                "SELECT * FROM corrections WHERE active = 1 AND field != ? ORDER BY id", (EXCLUDED,)
            ).fetchall()

    def decisions(self) -> dict[str, str]:
        """finding_key -> décision active."""
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT finding_key, decision FROM decisions WHERE active = 1"
            ).fetchall()
        return {r["finding_key"]: r["decision"] for r in rows}

    def original_frame(self) -> pd.DataFrame:
        return self._original.copy()

    def working_frame(self, include_excluded: bool = False) -> pd.DataFrame:
        """Original + corrections actives ; les enregistrements exclus sont retirés par défaut."""
        frame = self._original.copy()
        position = {rid: i for i, rid in enumerate(frame["record_id"].astype(str))}
        for row in self.active_corrections():
            frame.at[position[row["record_id"]], row["field"]] = row["new_value"]
        if not include_excluded:
            frame = frame[~frame["record_id"].isin(self.excluded_records())].reset_index(drop=True)
        return frame
