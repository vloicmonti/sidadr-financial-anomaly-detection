"""Mesure de la détection d'anomalies contre le manifeste des défauts injectés."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sidadr.anomaly_detection import AnomalyReport
from sidadr.ingestion.importer import _slug

#: Défauts injectés dont le montant est réellement hors norme (cibles légitimes du détecteur).
EXTREME_AMOUNT_DEFECTS = ("atypical_amount", "fee_above_cap")


@dataclass(frozen=True)
class AnomalyEvaluation:
    injected_atypical: int
    found_atypical: int
    flagged: int
    explained_by_other_defects: int  # p. ex. frais au-dessus du plafond : réellement extrêmes
    unexplained: int  # signalements sans défaut injecté correspondant

    @property
    def recall(self) -> float:
        return self.found_atypical / self.injected_atypical if self.injected_atypical else 1.0

    @property
    def precision(self) -> float:
        """Part des signalements qui correspondent à un montant injecté hors norme."""
        return (self.flagged - self.unexplained) / self.flagged if self.flagged else 1.0


def load_defects(path: Path) -> dict[str, set[str]]:
    """defect_type -> ensemble de record_id (identifiants tels que produits par l'import)."""
    defects: dict[str, set[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            defects.setdefault(row["defect_type"], set()).add(f"{_slug(row['file'])}:{row['row']}")
    return defects


def evaluate_anomalies(report: AnomalyReport, defects: dict[str, set[str]]) -> AnomalyEvaluation:
    flagged = {a.record_id for a in report.anomalies}
    atypical = defects.get("atypical_amount", set())
    extreme = set().union(*(defects.get(d, set()) for d in EXTREME_AMOUNT_DEFECTS))
    return AnomalyEvaluation(
        injected_atypical=len(atypical),
        found_atypical=len(flagged & atypical),
        flagged=len(flagged),
        explained_by_other_defects=len((flagged & extreme) - atypical),
        unexplained=len(flagged - extreme),
    )
