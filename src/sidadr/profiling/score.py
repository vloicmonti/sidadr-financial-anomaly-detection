"""Score qualité explicable : chaque dimension expose son numérateur, son dénominateur
et son poids, pour que le score puisse être recalculé à la main.
"""

from __future__ import annotations

from dataclasses import dataclass

from sidadr.config import Dimension, load_quality_dimensions
from sidadr.profiling.profile import DatasetProfile


@dataclass(frozen=True)
class DimensionResult:
    name: str
    label: str
    definition: str
    weight: float  # poids configuré
    effective_weight: float  # poids après renormalisation (0 si non évaluée)
    value: float | None  # entre 0 et 1, None si non évaluée
    numerator: int  # cellules ou lignes en défaut
    denominator: int  # cellules ou lignes examinées
    note: str = ""

    @property
    def evaluated(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class QualityScore:
    score: float  # 0 à 100
    dimensions: tuple[DimensionResult, ...]
    notes: tuple[str, ...]

    def __getitem__(self, name: str) -> DimensionResult:
        return next(d for d in self.dimensions if d.name == name)


def _measure(name: str, profile: DatasetProfile) -> tuple[float | None, int, int, str]:
    if name == "completeness":
        required = [c for c in profile.columns if c.required]
        bad = sum(c.missing for c in required)
        total = profile.n_rows * len(required)
        return (1 - bad / total if total else 1.0), bad, total, ""
    if name == "uniqueness":
        bad, total = profile.duplicate_rows, profile.n_rows
        return (1 - bad / total if total else 1.0), bad, total, ""
    if name == "validity":
        checked = [c for c in profile.columns if c.checked_for_validity]
        bad = sum(c.invalid for c in checked)
        total = sum(c.filled for c in checked)
        return (1 - bad / total if total else 1.0), bad, total, ""
    if name == "consistency":
        return None, 0, 0, "Règles métier pas encore disponibles (Phase 8)."
    return None, 0, 0, f"Dimension « {name} » inconnue du moteur."


def compute_score(
    profile: DatasetProfile, dimensions: tuple[Dimension, ...] | None = None
) -> QualityScore:
    dimensions = dimensions or load_quality_dimensions()
    measured = [(d, *_measure(d.name, profile)) for d in dimensions]
    evaluated_weight = sum(d.weight for d, value, *_ in measured if value is not None)

    results = []
    notes = []
    for d, value, bad, total, note in measured:
        effective = d.weight / evaluated_weight if value is not None and evaluated_weight else 0.0
        results.append(
            DimensionResult(
                d.name, d.label, d.definition, d.weight, effective, value, bad, total, note
            )
        )
        if value is None:
            notes.append(f"{d.label} non évaluée : {note} Les autres poids sont renormalisés.")

    score = 100 * sum(r.effective_weight * (r.value or 0.0) for r in results)
    notes.append(
        "Indicateur interne paramétrable, pas une norme. "
        "Les valeurs atypiques ne le pénalisent pas."
    )
    return QualityScore(round(score, 1), tuple(results), tuple(notes))
