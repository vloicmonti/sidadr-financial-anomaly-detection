"""Module C — doublons exacts."""

from sidadr.deduplication.detector import (
    DuplicateGroup,
    DuplicateKind,
    DuplicateReport,
    FieldDifference,
    detect_duplicates,
)

__all__ = [
    "DuplicateGroup",
    "DuplicateKind",
    "DuplicateReport",
    "FieldDifference",
    "detect_duplicates",
]
