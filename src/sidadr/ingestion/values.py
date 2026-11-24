"""Interprétation des valeurs texte : nombres, dates, types de colonnes.

Principes :
- une valeur n'est jamais « devinée » en silence : les choix faits au niveau d'une
  colonne (format de date, séparateur décimal) sont renvoyés avec leurs justifications ;
- une date qui a la forme d'une date mais n'existe pas au calendrier (30/02/2025) est
  distinguée d'une valeur qui n'est pas une date du tout.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

import pandas as pd

# --------------------------------------------------------------------------- nombres

_SPACES = re.compile(r"[\s  ]")
_NUMBER_BODY = re.compile(r"\d[\d.,]*")


def missing_marker_set(markers: Iterable[str]) -> frozenset[str]:
    """Normalise une liste de valeurs de remplissage pour la comparaison (casse, espaces)."""
    return frozenset(m.strip().upper() for m in markers if m.strip())


def placeholder(value: object, markers: frozenset[str]) -> str | None:
    """La valeur de remplissage reconnue (« UNKNOWN »…), telle qu'écrite, ou None."""
    if markers and isinstance(value, str) and value.strip().upper() in markers:
        return value.strip()
    return None


def is_missing(value: object, markers: frozenset[str] = frozenset()) -> bool:
    """Vide, espaces seuls, NA pandas, ou valeur de remplissage reconnue."""
    if value is None or value is pd.NA:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    if isinstance(value, str):
        return not value.strip() or placeholder(value, markers) is not None
    return False


class DecimalSeparator(StrEnum):
    POINT = "."
    COMMA = ","


def _is_ambiguous(body: str) -> bool:
    """Un seul séparateur, suivi d'exactement 3 chiffres : « 1,250 » ou « 1.250 »."""
    seps = [c for c in body if c in ",."]
    return len(seps) == 1 and len(body.split(seps[0])[1]) == 3


def parse_decimal(text: str | None, separator: DecimalSeparator | None = None) -> Decimal | None:
    """Convertit un montant texte en Decimal, ou None s'il n'est pas numérique.

    Gère les espaces de milliers (« 1 250 000 »), la virgule ou le point décimal et les
    formats mixtes (« 1.250.000,50 », « 1,250,000.50 »). Pour un cas réellement ambigu
    (« 1,250 »), le séparateur décimal de la colonne doit être fourni ; à défaut, le
    point est décimal et la virgule sépare les milliers.
    """
    if text is None:
        return None
    t = _SPACES.sub("", text)
    negative = t.startswith("-")
    if t[:1] in "+-":
        t = t[1:]
    if not t or not _NUMBER_BODY.fullmatch(t) or t[-1] in ".,":
        return None

    if "," in t and "." in t:
        decimal_sep = "," if t.rfind(",") > t.rfind(".") else "."
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        if t.count(sep) > 1:
            decimal_sep = ""  # plusieurs occurrences : séparateur de milliers
        elif _is_ambiguous(t):
            decimal_sep = sep if (separator.value if separator else ".") == sep else ""
        else:
            decimal_sep = sep
    else:
        decimal_sep = ""

    integer_part, _, fraction = t.partition(decimal_sep) if decimal_sep else (t, "", "")
    if decimal_sep and (decimal_sep in fraction or any(c in fraction for c in ",.")):
        return None
    for sep in {",", "."} - {decimal_sep}:
        groups = integer_part.split(sep)
        if len(groups) > 1 and (
            any(len(g) != 3 for g in groups[1:]) or not 1 <= len(groups[0]) <= 3
        ):
            return None  # séparateurs de milliers mal placés : « 12,34,5 »
        integer_part = integer_part.replace(sep, "")
    try:
        value = Decimal(f"{integer_part}.{fraction}" if fraction else integer_part)
    except InvalidOperation:
        return None
    return -value if negative else value


def infer_decimal_separator(values: list[str]) -> tuple[DecimalSeparator | None, str]:
    """Déduit le séparateur décimal d'une colonne à partir des valeurs non ambiguës."""
    votes: Counter[DecimalSeparator] = Counter()
    for v in values:
        t = _SPACES.sub("", v).lstrip("+-")
        if "," in t and "." in t:
            votes[
                DecimalSeparator.COMMA if t.rfind(",") > t.rfind(".") else DecimalSeparator.POINT
            ] += 1
        elif t.count(",") == 1 and "." not in t and not _is_ambiguous(t):
            votes[DecimalSeparator.COMMA] += 1
        elif t.count(".") == 1 and "," not in t and not _is_ambiguous(t):
            votes[DecimalSeparator.POINT] += 1
    if not votes:
        return None, "aucune décimale observée"
    (winner, n), *rest = votes.most_common()
    note = f"séparateur décimal « {winner.value} » ({n} valeurs)"
    if rest:
        note += f", {rest[0][1]} valeurs avec l'autre séparateur"
    return winner, note


# --------------------------------------------------------------------------- dates

_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?")
_SLASHED = re.compile(r"(\d{1,2})([/.\-])(\d{1,2})\2(\d{4})")


#: Part maximale de valeurs contradictoires pour considérer l'ordre d'une colonne comme sûr.
MIXED_ORDER_TOLERANCE = 0.05


class DateOrder(StrEnum):
    ISO = "YYYY-MM-DD"
    DMY = "DD/MM/YYYY"
    MDY = "MM/DD/YYYY"


class DateStatus(StrEnum):
    VALID = "valid"
    INVALID_CALENDAR = "invalid_calendar"  # forme de date, mais jour/mois impossible
    NOT_A_DATE = "not_a_date"


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(text: str | None, order: DateOrder | None = None) -> tuple[DateStatus, date | None]:
    """Interprète une date selon l'ordre de la colonne (ISO toujours accepté)."""
    if text is None:
        return DateStatus.NOT_A_DATE, None
    t = text.strip()
    if m := _ISO.fullmatch(t):
        parsed = _make_date(int(m[1]), int(m[2]), int(m[3]))
        return (DateStatus.VALID, parsed) if parsed else (DateStatus.INVALID_CALENDAR, None)
    if m := _SLASHED.fullmatch(t):
        a, b, year = int(m[1]), int(m[3]), int(m[4])
        day, month = (b, a) if order is DateOrder.MDY else (a, b)
        parsed = _make_date(year, month, day)
        return (DateStatus.VALID, parsed) if parsed else (DateStatus.INVALID_CALENDAR, None)
    return DateStatus.NOT_A_DATE, None


@dataclass(frozen=True)
class DateOrderDecision:
    order: DateOrder | None
    ambiguous: bool
    note: str


def infer_date_order(values: list[str]) -> DateOrderDecision:
    """Décide si les dates « a/b/AAAA » d'une colonne sont jour/mois ou mois/jour."""
    slashed = [m for v in values if (m := _SLASHED.fullmatch(v.strip()))]
    iso = sum(1 for v in values if _ISO.fullmatch(v.strip()))
    if not slashed:
        return DateOrderDecision(DateOrder.ISO if iso else None, False, "dates au format ISO")
    first_gt_12 = sum(1 for m in slashed if int(m[1]) > 12)
    second_gt_12 = sum(1 for m in slashed if int(m[3]) > 12)
    if first_gt_12 and not second_gt_12:
        return DateOrderDecision(
            DateOrder.DMY, False, f"jour en premier ({first_gt_12} valeurs avec jour > 12)"
        )
    if second_gt_12 and not first_gt_12:
        return DateOrderDecision(
            DateOrder.MDY, False, f"mois en premier ({second_gt_12} valeurs avec jour > 12)"
        )
    if first_gt_12 and second_gt_12:
        order = DateOrder.DMY if first_gt_12 >= second_gt_12 else DateOrder.MDY
        minority = min(first_gt_12, second_gt_12)
        share = minority / (first_gt_12 + second_gt_12)
        label = "jour en premier" if order is DateOrder.DMY else "mois en premier"
        if share <= MIXED_ORDER_TOLERANCE:
            return DateOrderDecision(
                order,
                False,
                f"{label} ; {minority} valeurs incompatibles signalées comme dates impossibles",
            )
        return DateOrderDecision(
            order, True, f"ordres jour/mois et mois/jour mélangés : {label} retenu, à vérifier"
        )
    return DateOrderDecision(
        DateOrder.DMY,
        True,
        "aucune valeur ne tranche (jour et mois <= 12) : jour/mois retenu, à vérifier",
    )


# --------------------------------------------------------------------------- types de valeur


class ValueKind(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    INVALID_DATE = "invalid_date"
    BOOLEAN = "boolean"
    TEXT = "text"


_BOOLEANS = {"true", "false", "vrai", "faux", "oui", "non", "yes", "no"}


def classify(text: str, order: DateOrder | None = None) -> ValueKind:
    status, _ = parse_date(text, order)
    if status is DateStatus.VALID:
        return ValueKind.DATE
    if status is DateStatus.INVALID_CALENDAR:
        return ValueKind.INVALID_DATE
    number = parse_decimal(text)
    if number is not None:
        return ValueKind.INTEGER if number == number.to_integral_value() else ValueKind.DECIMAL
    if text.strip().lower() in _BOOLEANS:
        return ValueKind.BOOLEAN
    return ValueKind.TEXT


@dataclass
class TypeInference:
    """Type dominant d'une colonne, calculé sur les cellules renseignées."""

    dominant: ValueKind | None
    counts: dict[ValueKind, int] = field(default_factory=dict)
    non_empty: int = 0

    @property
    def conformity(self) -> float:
        if not self.non_empty or self.dominant is None:
            return 1.0
        compatible = {self.dominant}
        if self.dominant in (ValueKind.INTEGER, ValueKind.DECIMAL):
            compatible = {ValueKind.INTEGER, ValueKind.DECIMAL}
        return sum(self.counts.get(k, 0) for k in compatible) / self.non_empty


def infer_type(values: list[str | None]) -> TypeInference:
    present = [v for v in values if v is not None and v.strip()]
    if not present:
        return TypeInference(None)
    order = infer_date_order(present).order
    counts = Counter(classify(v, order) for v in present)
    merged = Counter(counts)
    if merged[ValueKind.INVALID_DATE]:
        merged[ValueKind.DATE] += merged.pop(ValueKind.INVALID_DATE)
    numeric = merged[ValueKind.INTEGER] + merged[ValueKind.DECIMAL]
    best_kind, best_count = merged.most_common(1)[0]
    if numeric > best_count:
        best_kind = ValueKind.DECIMAL if merged[ValueKind.DECIMAL] else ValueKind.INTEGER
    return TypeInference(best_kind, dict(counts), len(present))
