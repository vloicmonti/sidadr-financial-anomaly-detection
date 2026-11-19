"""Module E — clients potentiellement présents dans plusieurs banques ou agences.

Les correspondances de noms (module D) forment un graphe entre fiches clients ; chaque
composante connexe qui couvre plusieurs banques (ou plusieurs agences d'une banque) devient
un groupe à examiner.

Un groupe peut être relié **indirectement** : si A ressemble à B et B à C, A et C sont
dans le même groupe sans avoir été comparés directement. Le taux de liens directs est
affiché pour que l'utilisateur le voie. Rien n'est fusionné.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sidadr.config import CrossEntitySettings
from sidadr.findings import Finding, Severity
from sidadr.name_matching import WARNING, Category, CustomerProfile, MatchReport, NameMatch

_ORDER = list(Category)  # HIGH, INTERMEDIATE, LOW
_ACCEPTED = {
    "high": {Category.HIGH},
    "intermediate": {Category.HIGH, Category.INTERMEDIATE},
    "low": {Category.HIGH, Category.INTERMEDIATE, Category.LOW},
}


class EntityScope(StrEnum):
    MULTI_BANK = "multi_bank"
    MULTI_BRANCH = "multi_branch"

    @property
    def label(self) -> str:
        return (
            "Plusieurs banques"
            if self is EntityScope.MULTI_BANK
            else "Plusieurs agences d'une banque"
        )


class FieldAgreement(StrEnum):
    CONCORDANT = "concordant"  # toutes les fiches renseignées ont la même valeur
    PARTIAL = "partial"  # même valeur là où elle est renseignée, mais certaines fiches vides
    DISCORDANT = "discordant"  # au moins deux valeurs différentes
    UNKNOWN = "unknown"  # aucune fiche ne renseigne le champ


@dataclass(frozen=True)
class FieldComparison:
    field: str
    agreement: FieldAgreement
    values: tuple[tuple[str, str | None], ...]  # (fiche, valeur)


@dataclass(frozen=True)
class EntityGroup:
    group_id: str
    scope: EntityScope
    members: tuple[CustomerProfile, ...]
    links: tuple[NameMatch, ...]
    fields: tuple[FieldComparison, ...]

    @property
    def banks(self) -> tuple[str, ...]:
        return tuple(sorted({m.bank_code or "?" for m in self.members}))

    @property
    def branches(self) -> tuple[str, ...]:
        return tuple(sorted({m.branch_code or "?" for m in self.members}))

    @property
    def confidence(self) -> Category:
        """Catégorie du lien le plus faible du groupe."""
        return max((link.category for link in self.links), key=_ORDER.index)

    @property
    def direct_link_ratio(self) -> float:
        n = len(self.members)
        return len(self.links) / (n * (n - 1) / 2)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(r for m in self.members for r in m.record_ids)

    def explanation(self) -> str:
        names = " / ".join(dict.fromkeys(m.name for m in self.members))
        where = (
            ", ".join(self.banks)
            if self.scope is EntityScope.MULTI_BANK
            else ", ".join(self.branches)
        )
        parts = [
            f"{len(self.members)} fiches ({names}) dans {where}",
            f"lien le plus faible : {self.confidence.label.lower()}",
        ]
        if self.direct_link_ratio < 1:
            parts.append(
                f"rapprochement en partie indirect ({self.direct_link_ratio:.0%} des paires comparées directement)"
            )
        discordant = [f.field for f in self.fields if f.agreement is FieldAgreement.DISCORDANT]
        if discordant:
            parts.append(f"champs divergents : {', '.join(discordant)}")
        return " ; ".join(parts) + "."

    def to_finding(self) -> Finding:
        return Finding(
            finding_id=f"ENT-{self.group_id}",
            kind=self.scope.value,
            severity=Severity.REVIEW_ALERT,
            record_ids=self.record_ids,
            rule_id=f"ENTITY:{self.scope.value}",
            explanation=self.explanation(),
            recommendation="Valider, rejeter ou scinder le groupe après comparaison des fiches. "
            + WARNING,
            field="customer_name",
            observed_value=" | ".join(m.label for m in self.members),
            threshold=self.confidence.label,
        )


@dataclass
class EntityReport:
    groups: list[EntityGroup]
    settings: CrossEntitySettings
    links_used: int

    def by_scope(self, scope: EntityScope) -> list[EntityGroup]:
        return [g for g in self.groups if g.scope is scope]

    def findings(self) -> list[Finding]:
        return [g.to_finding() for g in self.groups]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _compare_field(members: tuple[CustomerProfile, ...], name: str) -> FieldComparison:
    values = tuple((m.label, getattr(m, name)) for m in members)
    present = {v for _label, v in values if v}
    if not present:
        agreement = FieldAgreement.UNKNOWN
    elif len(present) > 1:
        agreement = FieldAgreement.DISCORDANT
    elif all(v for _label, v in values):
        agreement = FieldAgreement.CONCORDANT
    else:
        agreement = FieldAgreement.PARTIAL
    return FieldComparison(name, agreement, values)


def _make_group(
    members: tuple[CustomerProfile, ...],
    links: tuple[NameMatch, ...],
    settings: CrossEntitySettings,
) -> EntityGroup | None:
    banks = {m.bank_code for m in members}
    branches = {m.branch_code for m in members}
    if len(banks) > 1:
        scope = EntityScope.MULTI_BANK
    elif len(branches) > 1 and settings.include_multi_branch:
        scope = EntityScope.MULTI_BRANCH
    else:
        return None
    fields = tuple(_compare_field(members, f) for f in ("birth_date", "phone"))
    return EntityGroup("", scope, members, tuple(sorted(links, key=lambda m: m.match_id)), fields)


def group_entities(
    matches: MatchReport, settings: CrossEntitySettings | None = None
) -> EntityReport:
    """Construit les groupes à examiner.

    - Les correspondances **élevées** (corroborées) sont regroupées transitivement.
    - Les correspondances **intermédiaires ou faibles** (si acceptées) restent des paires :
      sans corroboration, la transitivité enchaînerait des homonymes en groupes géants.
    """
    settings = settings or CrossEntitySettings()
    accepted = _ACCEPTED[settings.min_category]
    links = [m for m in matches.matches if m.category in accepted]
    strong = [m for m in links if m.category is Category.HIGH]
    weak = [m for m in links if m.category is not Category.HIGH]

    forest = _UnionFind()
    for link in strong:
        forest.union(link.left.key, link.right.key)
    profiles = {p.key: p for p in matches.profiles}
    components: dict[tuple[str, str], list[tuple[str, str]]] = {}
    links_by_root: dict[tuple[str, str], list[NameMatch]] = {}
    for link in strong:
        root = forest.find(link.left.key)
        members = components.setdefault(root, [])
        for key in (link.left.key, link.right.key):
            if key not in members:
                members.append(key)
        links_by_root.setdefault(root, []).append(link)

    groups: list[EntityGroup] = []
    for root in sorted(components):
        group = _make_group(
            tuple(profiles[k] for k in sorted(components[root])),
            tuple(links_by_root[root]),
            settings,
        )
        if group is not None:
            groups.append(group)
    for link in weak:
        if (
            forest.find(link.left.key) == forest.find(link.right.key)
            and link.left.key in forest.parent
        ):
            continue  # déjà dans un même groupe corroboré
        group = _make_group(
            tuple(sorted((link.left, link.right), key=lambda p: p.key)), (link,), settings
        )
        if group is not None:
            groups.append(group)

    groups.sort(
        key=lambda g: (_ORDER.index(g.confidence), g.scope.value, -len(g.members), g.members[0].key)
    )
    numbered = [
        EntityGroup(f"{n:05d}", g.scope, g.members, g.links, g.fields)
        for n, g in enumerate(groups, start=1)
    ]
    return EntityReport(numbered, settings, len(links))
