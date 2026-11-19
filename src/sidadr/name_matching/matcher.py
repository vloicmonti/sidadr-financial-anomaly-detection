"""Module D — rapprochement des noms de clients.

Pour chaque paire de fiches qui partagent une clé de blocage :

1. plusieurs stratégies comparent les noms normalisés (0 à 100) ;
2. le score de nom est le maximum des stratégies, et la stratégie gagnante est conservée ;
3. les champs corroborants (date de naissance, téléphone) ajustent ce score ;
4. le score final place la paire dans une catégorie (élevée, intermédiaire, faible).

`token_set_ratio` n'est volontairement pas utilisé : il donne 100 à « MANGA Jean » face à
« MANGA Jean Pierre », deux personnes possiblement distinctes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations

import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from sidadr.config import MatchingSettings, Schema, default_schema
from sidadr.findings import Finding, Severity
from sidadr.name_matching.normalize import block_keys
from sidadr.name_matching.profiles import CustomerProfile, build_profiles

WARNING = (
    "Une similarité de nom ne prouve pas une identité : des personnes distinctes peuvent "
    "porter des noms proches. Aucune fusion n'est appliquée sans validation humaine."
)


class Strategy(StrEnum):
    EXACT = "exact_normalized"  # noms identiques après normalisation
    TOKEN_SORT = "token_sort"  # mêmes mots dans un autre ordre
    LEVENSHTEIN = "levenshtein"  # fautes de frappe (distance d'édition, mots triés)
    JARO_WINKLER = "jaro_winkler"  # fautes de frappe (mot le moins ressemblant)
    COLLAPSED = "collapsed"  # mots collés ou séparés (JEANPIERRE / JEAN PIERRE)
    INITIALS = "initials_compatible"  # prénoms abrégés (J. P. MANGA)


class Category(StrEnum):
    HIGH = "high"
    INTERMEDIATE = "intermediate"
    LOW = "low"

    @property
    def label(self) -> str:
        return {
            "high": "Similarité élevée",
            "intermediate": "Similarité intermédiaire",
            "low": "Similarité faible",
        }[self.value]


class Corroboration(StrEnum):
    SUPPORTED = "supported"  # au moins un champ concorde, aucun ne contredit
    CONTRADICTED = "contradicted"  # au moins un champ diverge
    UNVERIFIABLE = "unverifiable"  # aucun champ comparable


@dataclass(frozen=True)
class Adjustment:
    field: str
    points: float
    reason: str


@dataclass(frozen=True)
class NameMatch:
    match_id: str
    left: CustomerProfile
    right: CustomerProfile
    scores: dict[Strategy, float]
    strategy: Strategy
    name_score: float
    adjustments: tuple[Adjustment, ...]
    final_score: float
    category: Category
    corroboration: Corroboration

    @property
    def same_bank(self) -> bool:
        return self.left.bank_code == self.right.bank_code

    def explanation(self) -> str:
        parts = [
            f"« {self.left.name} » et « {self.right.name} » : score de nom {self.name_score:.0f} ({self.strategy.value})"
        ]
        parts += [f"{a.reason} ({a.points:+.0f})" for a in self.adjustments]
        if self.corroboration is Corroboration.UNVERIFIABLE:
            parts.append("aucun champ corroborant comparable")
        return " ; ".join(parts) + f" -> score final {self.final_score:.0f}."

    def to_finding(self) -> Finding:
        return Finding(
            finding_id=f"NAME-{self.match_id}",
            kind="name_match",
            severity=Severity.REVIEW_ALERT,
            record_ids=self.left.record_ids + self.right.record_ids,
            rule_id=f"NAME:{self.strategy.value}",
            explanation=self.explanation(),
            recommendation="Comparer les fiches côte à côte et confirmer ou rejeter la correspondance. "
            + WARNING,
            field="customer_name",
            observed_value=f"{self.left.name} | {self.right.name}",
            threshold=self.category.label,
            score=self.final_score,
        )


@dataclass
class MatchReport:
    profiles: list[CustomerProfile]
    matches: list[NameMatch]
    candidate_pairs: set[tuple[int, int]]  # indices de profils comparés
    skipped_blocks: dict[str, int] = field(default_factory=dict)
    settings: MatchingSettings = field(default_factory=MatchingSettings)

    def by_category(self, category: Category) -> list[NameMatch]:
        return [m for m in self.matches if m.category is category]

    def counts(self) -> dict[str, int]:
        return {c.value: len(self.by_category(c)) for c in Category}

    def findings(self) -> list[Finding]:
        return [m.to_finding() for m in self.matches]


# --------------------------------------------------------------------------- stratégies


def _rotations(words: tuple[str, ...]) -> set[str]:
    return {"".join(words[i:] + words[:i]) for i in range(len(words))}


def collapsed_score(a: tuple[str, ...], b: tuple[str, ...]) -> float | None:
    """Compare les noms écrits sans espaces, dans toutes les rotations (nom/prénom)."""
    if len(a) == len(b):
        return None
    return max(fuzz.ratio(x, y) for x in _rotations(a) for y in _rotations(b))


def initials_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """« J P MANGA » est compatible avec « MANGA JEAN PIERRE » : chaque mot complet du nom
    abrégé se retrouve, et chaque initiale correspond à un mot restant distinct."""
    if len(a) != len(b):
        return False
    abbreviated = a if any(len(t) == 1 for t in a) else b if any(len(t) == 1 for t in b) else None
    if abbreviated is None:
        return False
    remaining = list(b if abbreviated is a else a)
    full_words = [t for t in abbreviated if len(t) > 1]
    if not full_words:
        return False  # que des initiales : trop peu d'information
    for word in full_words:
        match = next(
            (
                r
                for r in remaining
                if len(r) > 1 and JaroWinkler.normalized_similarity(word, r) >= 0.92
            ),
            None,
        )
        if match is None:
            return False
        remaining.remove(match)
    for initial in (t for t in abbreviated if len(t) == 1):
        match = next((r for r in remaining if r.startswith(initial)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def score_names(
    a: CustomerProfile, b: CustomerProfile, initials_score: float
) -> dict[Strategy, float]:
    if a.normalized == b.normalized:
        return {Strategy.EXACT: 100.0}
    sorted_a, sorted_b = sorted(a.tokens), sorted(b.tokens)
    scores = {
        Strategy.TOKEN_SORT: fuzz.token_sort_ratio(a.normalized, b.normalized),
        Strategy.LEVENSHTEIN: fuzz.ratio(" ".join(sorted_a), " ".join(sorted_b)),
    }
    if len(sorted_a) == len(sorted_b):
        # Jaro-Winkler mot à mot, en retenant le mot le moins ressemblant : appliqué à la
        # chaîne entière, il favorise trop les débuts communs (« ABENA JEAN » / « ABENA JOEL »).
        scores[Strategy.JARO_WINKLER] = 100 * min(
            JaroWinkler.normalized_similarity(x, y) for x, y in zip(sorted_a, sorted_b, strict=True)
        )
    collapsed = collapsed_score(a.tokens, b.tokens)
    if collapsed is not None:
        scores[Strategy.COLLAPSED] = collapsed
    if initials_compatible(a.tokens, b.tokens):
        scores[Strategy.INITIALS] = initials_score
    return {k: round(v, 1) for k, v in scores.items()}


def corroborate(
    a: CustomerProfile, b: CustomerProfile, settings: MatchingSettings
) -> tuple[tuple[Adjustment, ...], Corroboration]:
    adjustments = []
    if a.birth_date and b.birth_date:
        if a.birth_date == b.birth_date:
            adjustments.append(
                Adjustment("birth_date", settings.birth_date_match, "date de naissance identique")
            )
        else:
            adjustments.append(
                Adjustment(
                    "birth_date", settings.birth_date_mismatch, "dates de naissance différentes"
                )
            )
    if a.phone and b.phone:
        if a.phone == b.phone:
            adjustments.append(Adjustment("phone", settings.phone_match, "téléphone identique"))
        else:
            adjustments.append(
                Adjustment("phone", settings.phone_mismatch, "téléphones différents")
            )
    if not adjustments:
        status = Corroboration.UNVERIFIABLE
    elif any(adj.points < 0 for adj in adjustments):
        status = Corroboration.CONTRADICTED
    else:
        status = Corroboration.SUPPORTED
    return tuple(adjustments), status


def _category(score: float, settings: MatchingSettings) -> Category | None:
    if score >= settings.high:
        return Category.HIGH
    if score >= settings.intermediate:
        return Category.INTERMEDIATE
    if score >= settings.low:
        return Category.LOW
    return None


def _apply_caps(
    category: Category | None, corroboration: Corroboration, settings: MatchingSettings
) -> Category | None:
    """Plafonds de catégorie selon les champs corroborants.

    - Non vérifiable : au plus « intermédiaire ». Un nom seul ne suffit jamais à une
      similarité élevée ; deux homonymes sans autre champ comparable sont indiscernables.
    - Contredit (date de naissance ou téléphone différents) : au plus « faible ». La paire
      reste visible, car un téléphone peut changer, mais elle n'est plus prioritaire.
    """
    if category is None:
        return None
    order = list(Category)
    cap = None
    if corroboration is Corroboration.UNVERIFIABLE and settings.cap_unverifiable:
        cap = Category.INTERMEDIATE
    elif corroboration is Corroboration.CONTRADICTED and settings.cap_contradicted:
        cap = Category.LOW
    if cap is not None and order.index(category) < order.index(cap):
        return cap
    return category


# --------------------------------------------------------------------------- moteur


def candidate_pairs(
    profiles: list[CustomerProfile], max_block_size: int
) -> tuple[set[tuple[int, int]], dict[str, int]]:
    blocks: dict[str, list[int]] = defaultdict(list)
    for index, profile in enumerate(profiles):
        for key in block_keys(profile.tokens):
            blocks[key].append(index)
    pairs: set[tuple[int, int]] = set()
    skipped: dict[str, int] = {}
    for key, members in blocks.items():
        if len(members) > max_block_size:
            skipped[key] = len(members)
            continue
        pairs.update(combinations(members, 2))  # members est trié : i < j
    return pairs, skipped


def match_names(
    frame: pd.DataFrame, settings: MatchingSettings | None = None, schema: Schema | None = None
) -> MatchReport:
    settings = settings or MatchingSettings()
    schema = schema or default_schema()
    profiles = build_profiles(frame, schema, settings.phone_significant_digits)
    pairs, skipped = candidate_pairs(profiles, settings.max_block_size)

    matches = []
    for i, j in sorted(pairs):
        a, b = profiles[i], profiles[j]
        scores = score_names(a, b, settings.initials_score)
        strategy, name_score = max(
            scores.items(), key=lambda kv: (kv[1], -list(Strategy).index(kv[0]))
        )
        if (
            name_score + max(settings.birth_date_match, 0) + max(settings.phone_match, 0)
            < settings.low
        ):
            continue
        adjustments, corroboration = corroborate(a, b, settings)
        final = max(0.0, min(100.0, name_score + sum(adj.points for adj in adjustments)))
        category = _category(final, settings)
        category = _apply_caps(category, corroboration, settings)
        if category is None:
            continue
        matches.append(
            NameMatch(
                match_id="",
                left=a,
                right=b,
                scores=scores,
                strategy=strategy,
                name_score=name_score,
                adjustments=adjustments,
                final_score=round(final, 1),
                category=category,
                corroboration=corroboration,
            )
        )

    matches.sort(key=lambda m: (-m.final_score, m.left.key, m.right.key))
    numbered = [
        NameMatch(
            f"{n:05d}",
            m.left,
            m.right,
            m.scores,
            m.strategy,
            m.name_score,
            m.adjustments,
            m.final_score,
            m.category,
            m.corroboration,
        )
        for n, m in enumerate(matches, start=1)
    ]
    return MatchReport(profiles, numbered, pairs, skipped, settings)
