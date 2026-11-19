"""Module D — rapprochement des noms de clients."""

from sidadr.name_matching.matcher import (
    WARNING,
    Adjustment,
    Category,
    Corroboration,
    MatchReport,
    NameMatch,
    Strategy,
    match_names,
)
from sidadr.name_matching.normalize import normalize_name
from sidadr.name_matching.profiles import CustomerProfile, build_profiles

__all__ = [
    "WARNING",
    "Adjustment",
    "Category",
    "Corroboration",
    "CustomerProfile",
    "MatchReport",
    "NameMatch",
    "Strategy",
    "build_profiles",
    "match_names",
    "normalize_name",
]
