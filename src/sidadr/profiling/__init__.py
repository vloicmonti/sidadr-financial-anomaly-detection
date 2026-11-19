"""Module B — profilage et score qualité."""

from sidadr.profiling.profile import ColumnProfile, DatasetProfile, NumericStats, profile_frame
from sidadr.profiling.score import DimensionResult, QualityScore, compute_score

__all__ = [
    "ColumnProfile",
    "DatasetProfile",
    "DimensionResult",
    "NumericStats",
    "QualityScore",
    "compute_score",
    "profile_frame",
]
