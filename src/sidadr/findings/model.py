"""Format commun des constats produits par tous les détecteurs.

Un constat signale, il ne corrige rien : il attend une décision humaine (`status`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """Échelle de certitude. Les détecteurs statistiques et de similarité ne produisent
    jamais `CERTAIN_ERROR`."""

    CERTAIN_ERROR = "certain_error"  # contrainte impossible à respecter
    PROBABLE_INCONSISTENCY = "probable_inconsistency"  # règle violée, exception possible
    ATYPICAL_VALUE = "atypical_value"  # rare statistiquement, pas une erreur en soi
    REVIEW_ALERT = "review_alert"  # ressemblance qui demande un jugement humain

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def rank(self) -> int:
        """0 = plus grave ; sert au tri."""
        return list(Severity).index(self)


_LABELS = {
    Severity.CERTAIN_ERROR: "Erreur certaine",
    Severity.PROBABLE_INCONSISTENCY: "Incohérence probable",
    Severity.ATYPICAL_VALUE: "Valeur atypique",
    Severity.REVIEW_ALERT: "Alerte à examiner",
}


class FindingStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    KEPT_BOTH = "kept_both"
    CORRECTED = "corrected"


@dataclass(frozen=True)
class Finding:
    finding_id: str
    kind: str  # famille de détecteur : exact_duplicate_row, duplicate_key, …
    severity: Severity
    record_ids: tuple[str, ...]
    rule_id: str
    explanation: str
    recommendation: str
    field: str | None = None
    observed_value: str | None = None
    threshold: str | None = None
    score: float | None = None
    status: FindingStatus = FindingStatus.OPEN
