"""Correspondance entre les colonnes d'un fichier et les champs du schéma canonique.

Le système propose, l'utilisateur confirme : une correspondance non confirmée ne peut
pas être appliquée (`MappingError`). Chaque proposition indique sa méthode et sa
confiance pour que l'utilisateur sache ce qu'il valide.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum

from sidadr.config import Schema
from sidadr.ingestion.errors import MappingError

#: En dessous de ce score, une ressemblance de nom n'est pas proposée.
SIMILARITY_THRESHOLD = 0.80


class Method(StrEnum):
    SYNONYM = "synonym"  # en-tête identique (après normalisation) à un synonyme du schéma
    SIMILARITY = "similarity"  # en-tête proche d'un synonyme
    MANUAL = "manual"  # choisi ou corrigé par l'utilisateur
    IDENTITY = "identity"  # mode générique : la colonne devient un champ du même nom
    NONE = "none"


def normalize_header(header: str) -> str:
    """« Nom_Client », « nom client », « NOM-CLIENT » → « nomclient »."""
    text = unicodedata.normalize("NFKD", header)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", text)


@dataclass(frozen=True)
class Proposal:
    source: str
    target: str | None
    confidence: float
    method: Method
    note: str = ""


@dataclass(frozen=True)
class ColumnMapping:
    """Correspondance d'un fichier. `confirmed=False` tant que l'utilisateur n'a pas validé."""

    source_file: str
    proposals: tuple[Proposal, ...]
    confirmed: bool = False

    @property
    def assignments(self) -> dict[str, str | None]:
        return {p.source: p.target for p in self.proposals}

    def target_to_source(self) -> dict[str, str]:
        return {p.target: p.source for p in self.proposals if p.target is not None}

    def unmapped_sources(self) -> list[str]:
        return [p.source for p in self.proposals if p.target is None]

    def to_review(self) -> list[Proposal]:
        """Propositions qui méritent une attention particulière avant confirmation."""
        return [p for p in self.proposals if p.method is Method.SIMILARITY or p.note]

    def missing_required(self, schema: Schema) -> list[str]:
        mapped = set(self.target_to_source())
        return [name for name in schema.required if name not in mapped]

    def validate(self, schema: Schema) -> None:
        targets = [p.target for p in self.proposals if p.target is not None]
        unknown = sorted({t for t in targets if t not in schema.names})
        if unknown:
            raise MappingError(f"Champs inconnus du schéma : {', '.join(unknown)}.")
        doubled = sorted({t for t in targets if targets.count(t) > 1})
        if doubled:
            raise MappingError(
                f"Plusieurs colonnes associées au même champ : {', '.join(doubled)}."
            )
        missing = self.missing_required(schema)
        if missing:
            raise MappingError(f"Champs obligatoires sans colonne : {', '.join(missing)}.")

    def confirm(
        self, schema: Schema, overrides: dict[str, str | None] | None = None
    ) -> ColumnMapping:
        """Applique les corrections de l'utilisateur, vérifie la cohérence et confirme."""
        overrides = overrides or {}
        unknown_sources = sorted(set(overrides) - {p.source for p in self.proposals})
        if unknown_sources:
            raise MappingError(f"Colonnes absentes du fichier : {', '.join(unknown_sources)}.")
        proposals = tuple(
            replace(p, target=overrides[p.source], confidence=1.0, method=Method.MANUAL, note="")
            if p.source in overrides
            else p
            for p in self.proposals
        )
        confirmed = ColumnMapping(self.source_file, proposals, confirmed=True)
        confirmed.validate(schema)
        return confirmed


def propose_mapping(source_file: str, headers: list[str], schema: Schema) -> ColumnMapping:
    """Propose une correspondance pour chaque en-tête, sans jamais la confirmer."""
    synonyms: dict[str, str] = {}
    for f in schema.fields:
        for s in (f.name, *f.synonyms):
            synonyms.setdefault(normalize_header(s), f.name)

    candidates: list[Proposal] = []
    for header in headers:
        key = normalize_header(header)
        if key in synonyms:
            candidates.append(Proposal(header, synonyms[key], 1.0, Method.SYNONYM))
            continue
        best_target, best_score = None, 0.0
        for synonym, target in synonyms.items():
            score = SequenceMatcher(None, key, synonym).ratio()
            if score > best_score:
                best_target, best_score = target, score
        if best_target and best_score >= SIMILARITY_THRESHOLD:
            candidates.append(
                Proposal(
                    header,
                    best_target,
                    round(best_score, 2),
                    Method.SIMILARITY,
                    "nom proche : à vérifier",
                )
            )
        else:
            candidates.append(Proposal(header, None, 0.0, Method.NONE))

    # Deux colonnes pour un même champ : on garde la plus sûre, l'autre reste non associée.
    resolved: list[Proposal] = []
    for p in candidates:
        if p.target is None:
            resolved.append(p)
            continue
        rivals = [c for c in candidates if c.target == p.target]
        winner = max(rivals, key=lambda c: (c.confidence, -headers.index(c.source)))
        if winner is p:
            resolved.append(p)
        else:
            resolved.append(
                Proposal(
                    p.source,
                    None,
                    0.0,
                    Method.NONE,
                    f"conflit avec « {winner.source} » pour {p.target}",
                )
            )
    return ColumnMapping(source_file, tuple(resolved))
