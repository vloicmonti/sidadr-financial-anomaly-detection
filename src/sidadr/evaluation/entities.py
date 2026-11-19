"""Mesure du regroupement multi-banques contre la vérité terrain."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sidadr.cross_entity import EntityReport, EntityScope
from sidadr.evaluation.names import PersonTruth
from sidadr.name_matching import CustomerProfile


@dataclass(frozen=True)
class EntityEvaluation:
    true_multi_bank_persons: int  # personnes réellement clientes de plusieurs banques
    found_persons: int  # retrouvées dans un groupe multi-banques
    groups: int  # groupes multi-banques produits
    pure_groups: int  # groupes dont toutes les fiches sont la même personne

    @property
    def recall(self) -> float:
        return (
            self.found_persons / self.true_multi_bank_persons
            if self.true_multi_bank_persons
            else 1.0
        )

    @property
    def purity(self) -> float:
        return self.pure_groups / self.groups if self.groups else 1.0


def _person(profile: CustomerProfile, truth: PersonTruth) -> str | None:
    return truth.get((profile.bank_code or "", profile.customer_id))


def evaluate_entities(
    report: EntityReport, profiles: list[CustomerProfile], truth: PersonTruth
) -> EntityEvaluation:
    banks_of: dict[str, set[str]] = defaultdict(set)
    for p in profiles:
        person = _person(p, truth)
        if person is not None and p.bank_code:
            banks_of[person].add(p.bank_code)
    multi = {person for person, banks in banks_of.items() if len(banks) > 1}

    found: set[str] = set()
    groups = report.by_scope(EntityScope.MULTI_BANK)
    pure = 0
    for group in groups:
        persons = [_person(m, truth) for m in group.members]
        if len(set(persons)) == 1:
            pure += 1
        group_banks: dict[str, set[str]] = defaultdict(set)
        for member, person in zip(group.members, persons, strict=True):
            if person is not None and member.bank_code:
                group_banks[person].add(member.bank_code)
        found |= {
            person for person, banks in group_banks.items() if len(banks) > 1 and person in multi
        }
    return EntityEvaluation(len(multi), len(found), len(groups), pure)
