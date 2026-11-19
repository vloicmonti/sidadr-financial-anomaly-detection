"""Module B — profilage des champs canoniques.

Le profil décrit les données sans les modifier : complétude, unicité, conformité au type
attendu, statistiques, valeurs fréquentes. Les décisions d'interprétation (ordre des
dates, séparateur décimal) sont prises par fichier et consignées dans `decisions`.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd

from sidadr.config import FieldSpec, Schema, Settings, default_schema, default_settings
from sidadr.ingestion.values import (
    DateOrder,
    DateStatus,
    DecimalSeparator,
    infer_date_order,
    infer_decimal_separator,
    parse_date,
    parse_decimal,
)


@dataclass(frozen=True)
class NumericStats:
    count: int
    minimum: Decimal
    q1: Decimal
    median: Decimal
    q3: Decimal
    maximum: Decimal
    mean: float
    std: float


@dataclass
class ColumnProfile:
    name: str
    label: str
    expected_type: str
    required: bool
    n_rows: int
    missing: int
    distinct: int
    invalid: int = 0
    invalid_reasons: dict[str, int] = field(default_factory=dict)
    invalid_record_ids: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    numeric: NumericStats | None = None
    date_range: tuple[date, date] | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)

    @property
    def filled(self) -> int:
        return self.n_rows - self.missing

    @property
    def missing_rate(self) -> float:
        return self.missing / self.n_rows if self.n_rows else 0.0

    @property
    def conformity(self) -> float:
        return 1 - self.invalid / self.filled if self.filled else 1.0

    @property
    def checked_for_validity(self) -> bool:
        return self.expected_type in ("date", "decimal", "enum")


@dataclass
class DatasetProfile:
    n_rows: int
    imports: list[str]
    columns: list[ColumnProfile]
    duplicate_rows: int  # lignes impliquées dans un doublon de ligne complète
    duplicate_groups: int

    def __getitem__(self, name: str) -> ColumnProfile:
        return next(c for c in self.columns if c.name == name)


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value is pd.NA


def _quartiles(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    if len(values) < 2:
        return values[0], values[0], values[0]
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return Decimal(q1), Decimal(q2), Decimal(q3)


def _profile_column(frame: pd.DataFrame, spec: FieldSpec, settings: Settings) -> ColumnProfile:
    values = frame[spec.name].tolist()
    record_ids = frame["record_id"].tolist()
    import_ids = frame["import_id"].tolist()
    present = [
        (v, r, i)
        for v, r, i in zip(values, record_ids, import_ids, strict=True)
        if not _is_missing(v)
    ]
    profile = ColumnProfile(
        name=spec.name,
        label=spec.label,
        expected_type=spec.type,
        required=spec.required,
        n_rows=len(values),
        missing=len(values) - len(present),
        distinct=len({str(v) for v, _, _ in present}),
    )
    reasons: Counter[str] = Counter()

    def flag(record_id: str, reason: str) -> None:
        reasons[reason] += 1
        profile.invalid_record_ids.append(record_id)

    by_import: dict[str, list[tuple[str, str]]] = {}
    for v, r, i in present:
        by_import.setdefault(str(i), []).append((str(v), str(r)))

    if spec.type == "date":
        parsed: list[date] = []
        for import_id, items in by_import.items():
            decision = infer_date_order([v for v, _ in items])
            profile.decisions.append(f"{import_id} : {decision.note}")
            for v, r in items:
                status, d = parse_date(v, decision.order)
                if status is DateStatus.VALID and d is not None:
                    parsed.append(d)
                else:
                    flag(
                        r,
                        "date impossible au calendrier"
                        if status is DateStatus.INVALID_CALENDAR
                        else "pas une date",
                    )
        if parsed:
            profile.date_range = (min(parsed), max(parsed))

    elif spec.type == "decimal":
        numbers: list[Decimal] = []
        for import_id, items in by_import.items():
            separator, note = infer_decimal_separator([v for v, _ in items])
            profile.decisions.append(f"{import_id} : {note}")
            for v, r in items:
                n = parse_decimal(v, separator)
                if n is None:
                    flag(r, "valeur non numérique")
                else:
                    numbers.append(n)
        if numbers:
            ordered = sorted(numbers)
            q1, median, q3 = _quartiles(ordered)
            floats = [float(n) for n in numbers]
            profile.numeric = NumericStats(
                count=len(numbers),
                minimum=ordered[0],
                q1=q1,
                median=median,
                q3=q3,
                maximum=ordered[-1],
                mean=statistics.fmean(floats),
                std=statistics.pstdev(floats) if len(floats) > 1 else 0.0,
            )

    elif spec.type == "enum":
        allowed = set(spec.values)
        for items in by_import.values():
            for v, r in items:
                if v.strip() not in allowed:
                    flag(r, "valeur hors liste autorisée")

    if spec.type in ("text", "enum"):
        counts = Counter(str(v) for v, _, _ in present)
        profile.top_values = counts.most_common(settings.profiling.top_values)

    profile.invalid = sum(reasons.values())
    profile.invalid_reasons = dict(reasons)
    return profile


def comparable_frame(frame: pd.DataFrame, schema: Schema) -> pd.DataFrame:
    """Valeurs rendues comparables entre fichiers : dates en ISO, montants normalisés,
    texte sans espaces de bord. Les valeurs non interprétables restent telles quelles.

    Sert à détecter les lignes identiques malgré des conventions d'export différentes
    (« 01/04/2025 » et « 2025-04-01 » désignent la même date). Les téléphones ne sont
    pas normalisés : deux formats d'indicatif différents restent deux valeurs différentes.
    """
    out = pd.DataFrame(index=frame.index, columns=list(schema.names), dtype=object)
    for _import_id, group in frame.groupby("import_id", sort=False):
        for spec in schema.fields:
            raw = group[spec.name].tolist()
            present = [str(v) for v in raw if not _is_missing(v)]
            converted: list[str | None]
            if spec.type == "date":
                order = infer_date_order(present).order
                converted = [_normalized_date(v, order) for v in raw]
            elif spec.type == "decimal":
                separator, _note = infer_decimal_separator(present)
                converted = [_normalized_decimal(v, separator) for v in raw]
            else:
                converted = [None if _is_missing(v) else str(v).strip() for v in raw]
            out.loc[group.index, spec.name] = converted
    return out


def _normalized_date(value: object, order: DateOrder | None) -> str | None:
    if _is_missing(value):
        return None
    status, parsed = parse_date(str(value), order)
    return parsed.isoformat() if status is DateStatus.VALID and parsed else str(value).strip()


def _normalized_decimal(value: object, separator: DecimalSeparator | None) -> str | None:
    if _is_missing(value):
        return None
    number = parse_decimal(str(value), separator)
    return str(number.normalize()) if number is not None else str(value).strip()


def profile_frame(
    frame: pd.DataFrame, schema: Schema | None = None, settings: Settings | None = None
) -> DatasetProfile:
    """Profile une table canonique (un fichier ou une session entière)."""
    schema = schema or default_schema()
    settings = settings or default_settings()
    columns = [_profile_column(frame, spec, settings) for spec in schema.fields]
    business = comparable_frame(frame, schema)
    duplicated = business.duplicated(keep=False)
    groups = business[duplicated].drop_duplicates().shape[0] if duplicated.any() else 0
    return DatasetProfile(
        n_rows=len(frame),
        imports=sorted(frame["import_id"].unique().tolist()),
        columns=columns,
        duplicate_rows=int(duplicated.sum()),
        duplicate_groups=int(groups),
    )


def typed_frame(frame: pd.DataFrame, schema: Schema) -> pd.DataFrame:
    """Valeurs converties dans leur type : `date`, `Decimal` ou texte sans espaces de bord.

    Une valeur absente ou illisible devient None. Les conventions (ordre des dates,
    séparateur décimal) sont décidées fichier par fichier, comme dans le profil.
    """
    out = pd.DataFrame(index=frame.index, columns=list(schema.names), dtype=object)
    for _import_id, group in frame.groupby("import_id", sort=False):
        for spec in schema.fields:
            raw = group[spec.name].tolist()
            present = [str(v) for v in raw if not _is_missing(v)]
            converted: list[object]
            if spec.type == "date":
                order = infer_date_order(present).order
                converted = []
                for v in raw:
                    status, parsed = parse_date(None if _is_missing(v) else str(v), order)
                    converted.append(parsed if status is DateStatus.VALID else None)
            elif spec.type == "decimal":
                separator, _note = infer_decimal_separator(present)
                converted = [
                    None if _is_missing(v) else parse_decimal(str(v), separator) for v in raw
                ]
            else:
                converted = [None if _is_missing(v) else str(v).strip() for v in raw]
            out.loc[group.index, spec.name] = pd.Series(converted, index=group.index, dtype=object)
    return out
