"""Fiches clients : une par identifiant client et par fichier importé.

Les noms sont comparés entre fiches clients, pas entre transactions : un client qui a
50 opérations n'est comparé qu'une fois.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from sidadr.config import Schema
from sidadr.name_matching.normalize import normalize_name, tokens
from sidadr.profiling.profile import comparable_frame

_DIGITS = re.compile(r"\D")


@dataclass(frozen=True)
class CustomerProfile:
    import_id: str
    customer_id: str
    bank_code: str | None
    branch_code: str | None
    source_file: str
    name: str  # nom tel que saisi (le plus fréquent pour ce client)
    normalized: str
    tokens: tuple[str, ...]
    birth_date: str | None  # ISO
    phone: str | None  # derniers chiffres significatifs
    record_ids: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.import_id, self.customer_id)

    @property
    def label(self) -> str:
        return f"{self.import_id}/{self.customer_id}"


def _most_common(values: list[object]) -> str | None:
    present = [str(v) for v in values if v is not None and str(v).strip()]
    if not present:
        return None
    counts = Counter(present)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def build_profiles(frame: pd.DataFrame, schema: Schema, phone_digits: int) -> list[CustomerProfile]:
    """Une fiche par (fichier, identifiant client) ; les lignes sans identifiant client
    ou sans nom sont ignorées."""
    frame = frame.reset_index(drop=True)
    comparable = comparable_frame(frame, schema)
    data = frame[
        [
            "import_id",
            "customer_id",
            "customer_name",
            "bank_code",
            "branch_code",
            "source_file",
            "record_id",
        ]
    ].copy()
    data["birth_iso"] = comparable["birth_date"]
    data["phone"] = frame["phone"]
    data = data[data["customer_id"].notna() & data["customer_name"].notna()]

    profiles = []
    for (import_id, customer_id), group in data.groupby(["import_id", "customer_id"], sort=True):
        name = _most_common(group["customer_name"].tolist())
        if name is None:
            continue
        normalized = normalize_name(name)
        if not normalized:
            continue
        phone_raw = _most_common(group["phone"].tolist())
        phone = _DIGITS.sub("", phone_raw)[-phone_digits:] if phone_raw else None
        profiles.append(
            CustomerProfile(
                import_id=str(import_id),
                customer_id=str(customer_id),
                bank_code=_most_common(group["bank_code"].tolist()),
                branch_code=_most_common(group["branch_code"].tolist()),
                source_file=str(group["source_file"].iloc[0]),
                name=name,
                normalized=normalized,
                tokens=tokens(normalized),
                birth_date=_most_common(group["birth_iso"].tolist()),
                phone=phone or None,
                record_ids=tuple(str(r) for r in group["record_id"]),
            )
        )
    return profiles
