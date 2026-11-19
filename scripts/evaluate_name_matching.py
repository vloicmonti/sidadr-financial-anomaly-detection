"""Mesure le rapprochement de noms contre la vérité terrain d'un jeu synthétique.

Exemple : python scripts/evaluate_name_matching.py --data data/synthetic
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sidadr.config import load_matching_settings
from sidadr.evaluation.names import evaluate_names, load_customer_truth, load_homonyms
from sidadr.ingestion import ImportSession
from sidadr.name_matching import match_names

FILES = ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args()

    session = ImportSession()
    for name in FILES:
        inspection = session.inspect(args.data / name)
        session.add(inspection, inspection.mapping.confirm(session.schema))
    report = match_names(session.frame, load_matching_settings(), session.schema)
    truth_file = args.data / "_truth" / "customers.csv"
    evaluation = evaluate_names(report, load_customer_truth(truth_file), load_homonyms(truth_file))

    print(
        f"Fiches clients : {len(report.profiles)}  |  paires comparées : {evaluation.candidate_pairs}"
    )
    print(f"Vraies paires (même personne) : {evaluation.true_pairs}")
    print(f"Rappel du blocage : {evaluation.blocking_recall:.1%}")
    print(
        f"Homonymes classés en similarité élevée : {evaluation.homonyms_flagged_high} / {evaluation.homonym_pairs}"
    )
    print(
        f"\n{'Catégorie':<18}{'Prédites':>9}{'VP':>6}{'FP':>6}{'FN':>6}{'Précision':>11}{'Rappel':>9}"
    )
    for m in evaluation.measures:
        print(
            f"{m.label:<18}{m.predicted:>9}{m.true_positives:>6}{m.false_positives:>6}"
            f"{m.false_negatives:>6}{m.precision:>11.1%}{m.recall:>9.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
