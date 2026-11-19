"""Mesure du rapprochement de noms contre la vérité terrain du jeu synthétique.

Ce module lit `_truth/customers.csv`, que le moteur n'utilise jamais pour détecter :
il sert uniquement à compter les vrais et faux positifs.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from sidadr.name_matching import Category, MatchReport, NameMatch

PersonTruth = dict[tuple[str, str], str]  # (bank_code, customer_id) -> person_id


def load_customer_truth(path: Path) -> PersonTruth:
    with path.open(encoding="utf-8", newline="") as handle:
        return {(r["bank_code"], r["customer_id"]): r["person_id"] for r in csv.DictReader(handle)}


@dataclass(frozen=True)
class Measure:
    label: str
    predicted: int
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        return self.true_positives / self.predicted if self.predicted else 1.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total else 1.0


@dataclass(frozen=True)
class NameEvaluation:
    true_pairs: int
    candidate_pairs: int
    blocking_recall: float  # part des vraies paires comparées par le moteur
    measures: tuple[Measure, ...]  # cumulées : élevée, >= intermédiaire, >= faible
    homonym_pairs: int
    homonyms_flagged_high: int

    def __getitem__(self, label: str) -> Measure:
        return next(m for m in self.measures if m.label == label)


def _person(report: MatchReport, index: int, truth: PersonTruth) -> str | None:
    p = report.profiles[index]
    return truth.get((p.bank_code or "", p.customer_id))


def _pair_key(m: NameMatch) -> frozenset[tuple[str, str]]:
    return frozenset({m.left.key, m.right.key})


def evaluate_names(
    report: MatchReport, truth: PersonTruth, homonym_of: dict[str, str] | None = None
) -> NameEvaluation:
    by_person: dict[str, list[int]] = defaultdict(list)
    for index in range(len(report.profiles)):
        person = _person(report, index, truth)
        if person is not None:
            by_person[person].append(index)
    true_pairs = {
        pair for members in by_person.values() for pair in combinations(sorted(members), 2)
    }
    true_keys = {frozenset({report.profiles[i].key, report.profiles[j].key}) for i, j in true_pairs}

    blocking_recall = (
        len(true_pairs & report.candidate_pairs) / len(true_pairs) if true_pairs else 1.0
    )

    measures = []
    levels = {
        "high": (Category.HIGH,),
        ">= intermediate": (Category.HIGH, Category.INTERMEDIATE),
        ">= low": (Category.HIGH, Category.INTERMEDIATE, Category.LOW),
    }
    for label, categories in levels.items():
        predicted = {_pair_key(m) for m in report.matches if m.category in categories}
        tp = len(predicted & true_keys)
        measures.append(
            Measure(label, len(predicted), tp, len(predicted) - tp, len(true_keys) - tp)
        )

    homonym_pairs, flagged = 0, 0
    if homonym_of:
        person_of = {
            report.profiles[i].key: _person(report, i, truth) for i in range(len(report.profiles))
        }
        twins = {frozenset({a, b}) for a, b in homonym_of.items()}
        homonym_pairs = len(twins)
        for m in report.by_category(Category.HIGH):
            persons = frozenset({person_of[m.left.key], person_of[m.right.key]})
            if persons in twins:
                flagged += 1
    return NameEvaluation(
        len(true_pairs),
        len(report.candidate_pairs),
        blocking_recall,
        tuple(measures),
        homonym_pairs,
        flagged,
    )


def load_homonyms(path: Path) -> dict[str, str]:
    """person_id du jumeau -> person_id de la personne dont il porte le nom."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["person_id"]: r["homonym_of"] for r in csv.DictReader(handle) if r["homonym_of"]}
