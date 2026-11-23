"""Module F — valeurs statistiquement atypiques.

Deux méthodes explicables, appliquées par groupe d'opérations comparables
(type d'opération × devise), sur le logarithme des montants :

- **z-score robuste** (Iglewicz et Hoaglin) : 0,6745 × (x − médiane) / MAD, où la MAD est
  la médiane des écarts absolus à la médiane. Contrairement au z-score classique, il n'est
  pas déformé par les valeurs extrêmes qu'il cherche à détecter.
- **IQR** (Tukey) : hors de [Q1 − k·IQR ; Q3 + k·IQR].

Chaque constat est une **valeur atypique** : jamais une erreur certaine, jamais une fraude.
Les montants négatifs ou nuls ne sont pas analysés ici (le logarithme n'existe pas) : ils
relèvent des règles métier.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from sidadr.config import AnomalySettings, Schema, default_schema
from sidadr.findings import Finding, Severity
from sidadr.ingestion.values import infer_decimal_separator, parse_decimal

ROBUST_Z = "robust_zscore"
IQR = "iqr"

RECOMMENDATION = (
    "Vérifier la pièce justificative ou le système source. Une valeur atypique n'est pas une "
    "erreur en soi : un montant élevé peut être légitime. Ce constat ne qualifie pas l'opération "
    "de fraude."
)


@dataclass(frozen=True)
class GroupStats:
    """Statistiques d'un groupe, exprimées dans l'unité d'origine (montants)."""

    group: tuple[str, ...]
    field: str
    count: int
    median: float
    mad_log: float  # MAD calculée sur l'échelle analysée
    q1: float
    q3: float
    lower_bound: float  # bornes IQR reconverties en montants
    upper_bound: float
    robust_z_bounds: tuple[float, float] | None  # montants équivalents au seuil du z-score


@dataclass(frozen=True)
class Anomaly:
    anomaly_id: str
    record_id: str
    field: str
    raw_value: str
    value: float
    group: tuple[str, ...]
    direction: str  # "high" ou "low"
    robust_z: float | None
    methods: tuple[str, ...]  # méthodes déclenchées
    stats: GroupStats

    @property
    def severity(self) -> Severity:
        return Severity.ATYPICAL_VALUE

    def threshold_text(self, settings: AnomalySettings) -> str:
        parts = []
        if ROBUST_Z in self.methods and self.robust_z is not None:
            parts.append(f"z robuste {self.robust_z:+.1f} (seuil ±{settings.robust_z_threshold:g})")
        if IQR in self.methods:
            parts.append(
                f"hors bornes IQR k={settings.iqr_k:g} "
                f"[{fmt_amount(self.stats.lower_bound)} ; {fmt_amount(self.stats.upper_bound)}]"
            )
        return " ; ".join(parts)

    def explanation(self, settings: AnomalySettings) -> str:
        group = " · ".join(self.group)
        ratio = self.value / self.stats.median if self.stats.median else float("nan")
        side = "au-dessus" if self.direction == "high" else "en dessous"
        times = (
            f"{ratio:.1f}".replace(".", ",")
            if ratio < 10
            else f"{round(ratio):,}".replace(",", NARROW_NBSP)
        )
        where = "de la colonne" if self.group == ("ensemble",) else f"du groupe {group}"
        return (
            f"{self.field} = {fmt_amount(self.value)}, soit {times} fois la médiane {where} "
            f"({fmt_amount(self.stats.median)} sur {self.stats.count} valeurs) : nettement {side} "
            f"des valeurs habituelles ; {self.threshold_text(settings)}."
        )

    def to_finding(self, settings: AnomalySettings) -> Finding:
        return Finding(
            finding_id=f"ANO-{self.anomaly_id}",
            kind="atypical_value",
            severity=self.severity,
            record_ids=(self.record_id,),
            rule_id=f"STAT:{'+'.join(self.methods)}",
            explanation=self.explanation(settings),
            recommendation=RECOMMENDATION,
            field=self.field,
            observed_value=self.raw_value,
            threshold=self.threshold_text(settings),
            score=self.robust_z,
        )


@dataclass
class AnomalyReport:
    anomalies: list[Anomaly]
    groups: list[GroupStats]
    skipped_groups: dict[tuple[str, ...], int]  # groupe -> effectif insuffisant
    excluded_values: int  # non numériques, nuls ou négatifs
    settings: AnomalySettings
    notes: list[str] = field(default_factory=list)

    def by_method(self, method: str) -> list[Anomaly]:
        return [a for a in self.anomalies if method in a.methods]

    def findings(self) -> list[Finding]:
        return [a.to_finding(self.settings) for a in self.anomalies]


NARROW_NBSP = chr(0x202F)  # espace fine insécable, séparateur de milliers français


def fmt_amount(value: float) -> str:
    """1234567.8 -> « 1 234 568 » ; 3.456 -> « 3,46 » (format français)."""
    if abs(value) >= 100:
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.2f}".replace(".", ",")


def _numeric_column(frame: pd.DataFrame, name: str) -> list[float | None]:
    """Montants interprétés fichier par fichier (séparateur décimal propre à chaque source)."""
    values: list[float | None] = [None] * len(frame)
    for _import_id, group in frame.groupby("import_id", sort=False):
        raw = [None if v is None else str(v) for v in group[name].tolist()]
        separator, _note = infer_decimal_separator([v for v in raw if v and v.strip()])
        for position, text in zip(group.index, raw, strict=True):
            number = parse_decimal(text, separator) if text else None
            values[int(position)] = float(number) if isinstance(number, Decimal) else None
    return values


def _group_key(frame: pd.DataFrame, fields: tuple[str, ...]) -> list[tuple[str, ...] | None]:
    if not fields:  # aucun regroupement : toutes les lignes forment un seul groupe
        return [("ensemble",)] * len(frame)
    keys: list[tuple[str, ...] | None] = []
    columns = [frame[f].tolist() for f in fields]
    for values in zip(*columns, strict=True):
        if any(v is None or not str(v).strip() for v in values):
            keys.append(None)
        else:
            keys.append(tuple(str(v).strip().upper() for v in values))
    return keys


def detect_anomalies(
    frame: pd.DataFrame, settings: AnomalySettings | None = None, schema: Schema | None = None
) -> AnomalyReport:
    settings = settings or AnomalySettings()
    schema = schema or default_schema()
    frame = frame.reset_index(drop=True)
    record_ids = [str(r) for r in frame["record_id"]]
    keys = _group_key(frame, settings.group_by)

    def scale(x: float) -> float:
        return math.log(x) if settings.log_transform else x

    def unscale(y: float) -> float:
        return math.exp(y) if settings.log_transform else y

    found: list[Anomaly] = []
    all_stats: list[GroupStats] = []
    skipped: dict[tuple[str, ...], int] = {}
    excluded = 0
    notes: list[str] = []

    for name in settings.fields:
        raw_values = frame[name].tolist()
        numbers = _numeric_column(frame, name)
        members: dict[tuple[str, ...], list[int]] = {}
        for i, (key, number) in enumerate(zip(keys, numbers, strict=True)):
            if key is None or number is None or (settings.log_transform and number <= 0):
                excluded += raw_values[i] is not None
                continue
            members.setdefault(key, []).append(i)

        for key in sorted(members):
            index = members[key]
            if len(index) < settings.min_group_size:
                skipped[key] = len(index)
                continue
            transformed = [scale(numbers[i]) for i in index]  # type: ignore[arg-type]
            median = statistics.median(transformed)
            mad = statistics.median(abs(x - median) for x in transformed)
            q1, _q2, q3 = statistics.quantiles(transformed, n=4, method="inclusive")
            spread = q3 - q1
            low, high = q1 - settings.iqr_k * spread, q3 + settings.iqr_k * spread
            z_bounds = None
            if mad > 0:
                delta = settings.robust_z_threshold * mad / 0.6745
                z_bounds = (unscale(median - delta), unscale(median + delta))
            stats = GroupStats(
                group=key,
                field=name,
                count=len(index),
                median=unscale(median),
                mad_log=mad,
                q1=unscale(q1),
                q3=unscale(q3),
                lower_bound=unscale(low),
                upper_bound=unscale(high),
                robust_z_bounds=z_bounds,
            )
            all_stats.append(stats)
            if mad == 0:
                notes.append(
                    f"Groupe {' · '.join(key)} : dispersion nulle, z-score robuste non calculable."
                )

            for i, x in zip(index, transformed, strict=True):
                methods = []
                z = 0.6745 * (x - median) / mad if mad > 0 else None
                if (
                    settings.robust_z_enabled
                    and z is not None
                    and abs(z) > settings.robust_z_threshold
                ):
                    methods.append(ROBUST_Z)
                if settings.iqr_enabled and spread > 0 and not low <= x <= high:
                    methods.append(IQR)
                if methods:
                    found.append(
                        Anomaly(
                            anomaly_id="",
                            record_id=record_ids[i],
                            field=name,
                            raw_value=str(raw_values[i]),
                            value=numbers[i],  # type: ignore[arg-type]
                            group=key,
                            direction="high" if x > median else "low",
                            robust_z=round(z, 2) if z is not None else None,
                            methods=tuple(methods),
                            stats=stats,
                        )
                    )

    if skipped:
        notes.append(
            f"{len(skipped)} groupe(s) de moins de {settings.min_group_size} opérations non analysés : "
            + ", ".join(f"{' · '.join(k)} ({n})" for k, n in sorted(skipped.items()))
        )
    found.sort(key=lambda a: (-abs(a.robust_z or 0), a.record_id))
    numbered = [
        Anomaly(
            f"{n:05d}",
            a.record_id,
            a.field,
            a.raw_value,
            a.value,
            a.group,
            a.direction,
            a.robust_z,
            a.methods,
            a.stats,
        )
        for n, a in enumerate(found, start=1)
    ]
    return AnomalyReport(numbered, all_stats, skipped, excluded, settings, notes)
