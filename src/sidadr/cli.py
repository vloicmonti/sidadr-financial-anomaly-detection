"""Ligne de commande SIDADR.

    sidadr generate --rows 20000 --seed 42 --out data/synthetic
    sidadr inspect FICHIER [FICHIER ...]
    sidadr profile FICHIER [...] (--accept-proposed | --mapping mapping.yaml) [--json out.json]

La correspondance des colonnes n'est jamais appliquée sans confirmation explicite :
`--accept-proposed` accepte les propositions telles qu'affichées par `inspect`,
`--mapping` fournit des corrections fichier par fichier.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from sidadr.config import ConfigError
from sidadr.ingestion import ImportRejected, ImportSession, Inspection, MappingError
from sidadr.profiling import DatasetProfile, QualityScore, compute_score, profile_frame

EXIT_OK, EXIT_ERROR, EXIT_NEEDS_CONFIRMATION = 0, 1, 2


def _pct(value: float) -> str:
    return f"{100 * value:5.1f} %"


def _print_inspection(inspection: Inspection, session: ImportSession) -> None:
    raw = inspection.raw
    print(f"\n=== {raw.source_file}")
    if raw.kind == "csv":
        sep = "tabulation" if raw.delimiter == "\t" else f"« {raw.delimiter} »"
        print(f"  Format : CSV, encodage {raw.encoding}, séparateur {sep}")
    else:
        print(f"  Format : XLSX, feuille « {raw.sheet} »")
    print(
        f"  Lignes lues : {raw.n_rows}  |  rejetées : {len(raw.rejected)}"
        f"  |  vides ignorées : {raw.skipped_blank_rows}"
    )
    for rejected in raw.rejected[:5]:
        print(f"    ligne {rejected.source_row} rejetée : {rejected.reason}")
    if inspection.empty_columns:
        print(f"  Colonnes entièrement vides : {', '.join(inspection.empty_columns)}")
    for warning in raw.warnings:
        print(f"  Avertissement : {warning}")
    print("  Correspondance proposée (non confirmée) :")
    for p in inspection.mapping.proposals:
        target = p.target or "(non associée)"
        detail = (
            f"{p.method.value}, confiance {p.confidence:.2f}"
            if p.target
            else p.note or "aucun champ proche"
        )
        flag = "  <- à vérifier" if p in inspection.mapping.to_review() else ""
        print(f"    {p.source:<20} -> {target:<18} ({detail}){flag}")
    missing = inspection.mapping.missing_required(session.schema)
    if missing:
        print(f"  Champs obligatoires sans colonne : {', '.join(missing)}")


def _print_profile(profile: DatasetProfile, score: QualityScore) -> None:
    print(
        f"\n=== Profil de la session : {profile.n_rows} enregistrements, "
        f"imports {', '.join(profile.imports)}"
    )
    print(
        f"  {'Champ':<18}{'Type':<9}{'Oblig.':<8}"
        f"{'Manquants':>10}{'Invalides':>10}{'Distincts':>10}"
    )
    for c in profile.columns:
        print(
            f"  {c.name:<18}{c.expected_type:<9}{'oui' if c.required else 'non':<8}"
            f"{c.missing:>10}{c.invalid:>10}{c.distinct:>10}"
        )
        for reason, n in c.invalid_reasons.items():
            print(f"      {n} x {reason} (ex. {', '.join(c.invalid_record_ids[:3])})")
        for decision in c.decisions:
            if "vérifier" in decision or "incompatibles" in decision:
                print(f"      décision : {decision}")
    print(
        "  Lignes impliquées dans un doublon de ligne complète : "
        f"{profile.duplicate_rows} ({profile.duplicate_groups} groupes)"
    )

    print(f"\n=== Score qualité : {score.score:.1f} / 100")
    for d in score.dimensions:
        if d.value is None:
            print(f"  {d.label:<12} non évaluée")
            continue
        print(
            f"  {d.label:<12}{_pct(d.value)}   poids {d.weight:.2f} -> {d.effective_weight:.3f}"
            f"   ({d.numerator} en défaut sur {d.denominator})"
        )
    for note in score.notes:
        print(f"  Note : {note}")


def _load_overrides(path: Path | None) -> dict[str, dict[str, str | None]]:
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise MappingError(f"{path} : format attendu {{fichier: {{colonne: champ ou null}}}}.")
    return {
        str(f): {str(k): (None if v is None else str(v)) for k, v in m.items()}
        for f, m in data.items()
    }


def _json_default(value: object) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def cmd_inspect(args: argparse.Namespace) -> int:
    session = ImportSession()
    for path in args.files:
        _print_inspection(session.inspect(path, args.sheet), session)
    return EXIT_OK


def cmd_profile(args: argparse.Namespace) -> int:
    session = ImportSession()
    overrides = _load_overrides(args.mapping)
    inspections = [session.inspect(path, args.sheet) for path in args.files]
    if not args.accept_proposed and args.mapping is None:
        for inspection in inspections:
            _print_inspection(inspection, session)
        print(
            "\nLa correspondance des colonnes doit être confirmée : relancez avec "
            "--accept-proposed, ou --mapping FICHIER.yaml pour la corriger."
        )
        return EXIT_NEEDS_CONFIRMATION
    for inspection in inspections:
        mapping = inspection.mapping.confirm(session.schema, overrides.get(inspection.source_file))
        table = session.add(inspection, mapping)
        report = table.report
        print(
            f"Importé : {report.source_file} -> {report.import_id} "
            f"({report.rows_imported} lignes, {len(report.rows_rejected)} rejetées)"
        )
    profile = profile_frame(session.frame, session.schema, session.settings)
    score = compute_score(profile)
    _print_profile(profile, score)
    if args.json:
        payload = {"profile": dataclasses.asdict(profile), "score": dataclasses.asdict(score)}
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print(f"\nProfil exporté : {args.json}")
    return EXIT_OK


def cmd_generate(args: argparse.Namespace) -> int:
    from sidadr.synthetic.cli import main as generate_main

    return generate_main(
        ["--rows", str(args.rows), "--seed", str(args.seed), "--out", str(args.out)]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidadr", description="SIDADR — qualité des données financières"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="générer le jeu de données synthétique")
    gen.add_argument("--rows", type=int, default=20_000)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--out", type=Path, default=Path("data/synthetic"))
    gen.set_defaults(func=cmd_generate)

    for name, func, text in (
        ("inspect", cmd_inspect, "lire des fichiers et proposer la correspondance des colonnes"),
        ("profile", cmd_profile, "importer des fichiers, profiler la session et calculer le score"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("files", nargs="+", type=Path)
        p.add_argument("--sheet", help="feuille à lire dans les fichiers XLSX")
        p.set_defaults(func=func)
        if name == "profile":
            group = p.add_mutually_exclusive_group()
            group.add_argument(
                "--accept-proposed", action="store_true", help="confirmer les propositions"
            )
            group.add_argument(
                "--mapping", type=Path, help="corrections YAML {fichier: {colonne: champ}}"
            )
            p.add_argument("--json", type=Path, help="exporter le profil et le score en JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ImportRejected as exc:
        print(f"Import refusé : {exc}", file=sys.stderr)
    except (MappingError, ConfigError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
