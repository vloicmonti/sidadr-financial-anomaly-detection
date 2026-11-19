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
import csv
import dataclasses
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from sidadr.config import (
    ConfigError,
    load_cross_entity_settings,
    load_duplicate_settings,
    load_matching_settings,
)
from sidadr.cross_entity import (
    EntityGroup,
    EntityReport,
    EntityScope,
    FieldAgreement,
    group_entities,
)
from sidadr.deduplication import DuplicateKind, DuplicateReport, detect_duplicates
from sidadr.deduplication.detector import _SEVERITY as _KIND_SEVERITY
from sidadr.ingestion import ImportRejected, ImportSession, Inspection, MappingError
from sidadr.name_matching import (
    WARNING,
    Category,
    Corroboration,
    CustomerProfile,
    MatchReport,
    match_names,
)
from sidadr.profiling import DatasetProfile, QualityScore, compute_score, profile_frame

EXIT_OK, EXIT_ERROR, EXIT_NEEDS_CONFIRMATION = 0, 1, 2

#: Niveaux de doublons qui comptent dans l'unicité du score (les combinaisons sont des alertes).
KEY_LEVELS = (DuplicateKind.EXACT_ROW, DuplicateKind.KEY, DuplicateKind.CROSS_FILE)

_KIND_LABELS = {
    DuplicateKind.EXACT_ROW: "Lignes strictement identiques",
    DuplicateKind.KEY: "Même identifiant, valeurs différentes",
    DuplicateKind.CROSS_FILE: "Même identifiant, fichiers différents",
    DuplicateKind.COMBINATION: "Même combinaison de champs",
}


def safe_cell(value: str) -> str:
    """Neutralise l'injection de formules dans un export ouvert par un tableur."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


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
        "  Lignes complètes identiques (indicatif ; détail par identifiant : sidadr duplicates) : "
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


def _load_session(args: argparse.Namespace) -> ImportSession | None:
    """Importe les fichiers si la correspondance est confirmée ; sinon affiche les
    propositions et renvoie None."""
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
        return None
    for inspection in inspections:
        mapping = inspection.mapping.confirm(session.schema, overrides.get(inspection.source_file))
        table = session.add(inspection, mapping)
        report = table.report
        print(
            f"Importé : {report.source_file} -> {report.import_id} "
            f"({report.rows_imported} lignes, {len(report.rows_rejected)} rejetées)"
        )
    return session


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
    )
    print(f"\nExport JSON : {path}")


def _detect(session: ImportSession) -> DuplicateReport:
    settings = load_duplicate_settings(schema=session.schema)
    return detect_duplicates(session.frame, settings, session.schema)


def cmd_profile(args: argparse.Namespace) -> int:
    session = _load_session(args)
    if session is None:
        return EXIT_NEEDS_CONFIRMATION
    profile = profile_frame(session.frame, session.schema, session.settings)
    duplicates = _detect(session)
    score = compute_score(profile, duplicate_rows=duplicates.rows_involved(KEY_LEVELS))
    _print_profile(profile, score)
    if args.json:
        _write_json(
            args.json, {"profile": dataclasses.asdict(profile), "score": dataclasses.asdict(score)}
        )
    return EXIT_OK


def _print_duplicates(report: DuplicateReport, limit: int) -> None:
    print(
        f"\n=== Doublons exacts : {len(report.groups)} groupes sur {report.n_rows} enregistrements"
    )
    for kind in DuplicateKind:
        groups = report.by_kind(kind)
        severity = _KIND_SEVERITY[kind]
        rows = report.rows_involved((kind,))
        print(
            f"  {_KIND_LABELS[kind]:<34} {len(groups):>5} groupes  {rows:>6} enreg.  [{severity.label}]"
        )
    for note in report.notes:
        print(f"  Note : {note}")
    ordered = sorted(report.groups, key=lambda g: (g.severity.rank, -g.size, g.group_id))
    if ordered and limit:
        print(f"\n  {min(limit, len(ordered))} premiers groupes :")
    for group in ordered[:limit]:
        print(f"  [{group.group_id}] {group.explanation()}")
        print(f"      sources : {', '.join(group.sources)}")
        for diff in group.differences[:3]:
            values = " / ".join(f"{v if v is not None else '(vide)'}" for _rid, v in diff.values)
            print(f"      {diff.field} : {values}")
    print("\n  Aucun enregistrement n'a été modifié ni supprimé.")


def _write_duplicates_csv(path: Path, report: DuplicateReport) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["group_id", "kind", "severity", "rule", "key_value", "record_id", "source",
             "differing_fields", "explanation"]
        )  # fmt: skip
        for g in report.groups:
            diffs = ", ".join(d.field for d in g.differences)
            for record_id, source in zip(g.record_ids, g.sources, strict=True):
                writer.writerow(
                    [g.group_id, g.kind.value, g.severity.value, g.rule, safe_cell(g.key_value),
                     record_id, source, diffs, g.explanation()]
                )  # fmt: skip
    print(f"Export CSV : {path}")


def cmd_duplicates(args: argparse.Namespace) -> int:
    session = _load_session(args)
    if session is None:
        return EXIT_NEEDS_CONFIRMATION
    report = _detect(session)
    _print_duplicates(report, args.limit)
    if args.csv:
        _write_duplicates_csv(args.csv, report)
    if args.json:
        _write_json(args.json, {"duplicates": dataclasses.asdict(report)})
    return EXIT_OK


_MIN_CATEGORY = {
    "high": (Category.HIGH,),
    "intermediate": (Category.HIGH, Category.INTERMEDIATE),
    "low": (Category.HIGH, Category.INTERMEDIATE, Category.LOW),
}


def _side(profile: CustomerProfile) -> list[str]:
    return [
        profile.name,
        f"{profile.bank_code or '?'} / {profile.branch_code or '?'}",
        profile.birth_date or "(non renseignée)",
        profile.phone or "(non renseigné)",
        f"{profile.label} ({len(profile.record_ids)} opérations)",
    ]


def _print_names(report: MatchReport, categories: tuple[Category, ...], limit: int) -> None:
    print(
        f"\n=== Noms similaires : {len(report.profiles)} fiches clients, {len(report.candidate_pairs)} paires comparées"
    )
    for category in Category:
        matches = report.by_category(category)
        cross = sum(1 for m in matches if not m.same_bank)
        print(
            f"  {category.label:<26} {len(matches):>5} paires  (dont {cross} entre banques différentes)"
        )
    if report.skipped_blocks:
        print(f"  Blocs trop grands ignorés : {report.skipped_blocks}")
    print(f"\n  Attention : {WARNING}")
    shown = [m for m in report.matches if m.category in categories][:limit]
    labels = ["Nom", "Banque / agence", "Naissance", "Téléphone", "Fiche"]
    for m in shown:
        print(
            f"\n  [{m.match_id}] {m.category.label} — score final {m.final_score:.0f} (nom {m.name_score:.0f}, {m.strategy.value})"
        )
        for label, a, b in zip(labels, _side(m.left), _side(m.right), strict=True):
            print(f"      {label:<16} {a:<36} | {b}")
        for adj in m.adjustments:
            print(f"      {adj.reason} ({adj.points:+.0f})")
        if m.corroboration is Corroboration.UNVERIFIABLE:
            print("      aucun champ corroborant comparable : catégorie plafonnée")


def _write_names_csv(path: Path, report: MatchReport, categories: tuple[Category, ...]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["match_id", "category", "final_score", "name_score", "strategy", "corroboration",
             "left_file", "left_customer", "left_bank", "left_name", "left_birth", "left_phone",
             "right_file", "right_customer", "right_bank", "right_name", "right_birth", "right_phone",
             "explanation"]
        )  # fmt: skip
        for m in report.matches:
            if m.category not in categories:
                continue
            row: list[object] = [
                m.match_id,
                m.category.value,
                m.final_score,
                m.name_score,
                m.strategy.value,
                m.corroboration.value,
            ]
            for p in (m.left, m.right):
                row += [p.import_id, p.customer_id, p.bank_code, p.name, p.birth_date, p.phone]
            row.append(m.explanation())
            writer.writerow([safe_cell(str(v)) if v is not None else "" for v in row])
    print(f"Export CSV : {path}")


def cmd_names(args: argparse.Namespace) -> int:
    session = _load_session(args)
    if session is None:
        return EXIT_NEEDS_CONFIRMATION
    report = match_names(session.frame, load_matching_settings(), session.schema)
    categories = _MIN_CATEGORY[args.category]
    _print_names(report, categories, args.limit)
    if args.csv:
        _write_names_csv(args.csv, report, categories)
    if args.json:
        _write_json(
            args.json,
            {
                "name_matches": [
                    dataclasses.asdict(m) for m in report.matches if m.category in categories
                ]
            },
        )
    print("\n  Aucune fiche n'a été fusionnée ni modifiée.")
    return EXIT_OK


_AGREEMENT_LABELS = {
    FieldAgreement.CONCORDANT: "concordant",
    FieldAgreement.PARTIAL: "concordant là où renseigné",
    FieldAgreement.DISCORDANT: "DIVERGENT",
    FieldAgreement.UNKNOWN: "non renseigné",
}


def _print_entities(report: EntityReport, groups: list[EntityGroup], limit: int) -> None:
    print(
        f"\n=== Clients multi-entités : {len(report.groups)} groupes (correspondances >= {report.settings.min_category})"
    )
    for scope in EntityScope:
        scoped = report.by_scope(scope)
        by_conf = {c.label: sum(1 for g in scoped if g.confidence is c) for c in Category}
        detail = ", ".join(f"{n} {label.lower()}" for label, n in by_conf.items() if n)
        print(f"  {scope.label:<32} {len(scoped):>5} groupes  ({detail or 'aucun'})")
    print(f"\n  Attention : {WARNING}")
    for group in groups[:limit]:
        print(f"\n  [{group.group_id}] {group.scope.label} — {group.confidence.label}")
        for m in group.members:
            print(
                f"      {m.label:<26} {m.bank_code or '?'}/{m.branch_code or '?':<10} {m.name:<28} "
                f"né(e) {m.birth_date or '?':<10} tél. {m.phone or '?':<10} {len(m.record_ids)} op."
            )
        for comparison in group.fields:
            print(f"      {comparison.field:<12} {_AGREEMENT_LABELS[comparison.agreement]}")
        print(f"      {group.explanation()}")


def _write_entities_csv(path: Path, groups: list[EntityGroup]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["group_id", "scope", "confidence", "direct_link_ratio", "import_id", "customer_id",
             "bank_code", "branch_code", "name", "birth_date", "phone", "operations", "explanation"]
        )  # fmt: skip
        for g in groups:
            for m in g.members:
                row: list[object] = [
                    g.group_id, g.scope.value, g.confidence.value, round(g.direct_link_ratio, 2),
                    m.import_id, m.customer_id, m.bank_code, m.branch_code, m.name, m.birth_date,
                    m.phone, len(m.record_ids), g.explanation(),
                ]  # fmt: skip
                writer.writerow([safe_cell(str(v)) if v is not None else "" for v in row])
    print(f"Export CSV : {path}")


def cmd_entities(args: argparse.Namespace) -> int:
    session = _load_session(args)
    if session is None:
        return EXIT_NEEDS_CONFIRMATION
    matches = match_names(session.frame, load_matching_settings(), session.schema)
    report = group_entities(matches, load_cross_entity_settings())
    groups = report.groups if args.scope == "all" else report.by_scope(EntityScope(args.scope))
    _print_entities(report, groups, args.limit)
    if args.csv:
        _write_entities_csv(args.csv, groups)
    if args.json:
        _write_json(args.json, {"entity_groups": [dataclasses.asdict(g) for g in groups]})
    print("\n  Aucune fiche n'a été fusionnée ni modifiée.")
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
        ("duplicates", cmd_duplicates, "détecter les doublons exacts de la session"),
        ("names", cmd_names, "rapprocher les noms de clients similaires"),
        (
            "entities",
            cmd_entities,
            "clients possiblement présents dans plusieurs banques ou agences",
        ),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("files", nargs="+", type=Path)
        p.add_argument("--sheet", help="feuille à lire dans les fichiers XLSX")
        p.set_defaults(func=func)
        if name == "inspect":
            continue
        group = p.add_mutually_exclusive_group()
        group.add_argument(
            "--accept-proposed", action="store_true", help="confirmer les propositions"
        )
        group.add_argument(
            "--mapping", type=Path, help="corrections YAML {fichier: {colonne: champ}}"
        )
        p.add_argument("--json", type=Path, help="exporter le résultat en JSON")
        if name == "duplicates":
            p.add_argument(
                "--csv", type=Path, help="exporter les groupes (une ligne par enregistrement)"
            )
            p.add_argument("--limit", type=int, default=10, help="nombre de groupes affichés")
        if name == "names":
            p.add_argument(
                "--category",
                choices=list(_MIN_CATEGORY),
                default="intermediate",
                help="catégorie minimale affichée et exportée",
            )
            p.add_argument("--csv", type=Path, help="exporter les paires (une ligne par paire)")
            p.add_argument("--limit", type=int, default=5, help="nombre de paires affichées")
        if name == "entities":
            p.add_argument(
                "--scope",
                choices=["all", *[s.value for s in EntityScope]],
                default="all",
                help="type de groupes affichés et exportés",
            )
            p.add_argument("--csv", type=Path, help="exporter les groupes (une ligne par fiche)")
            p.add_argument("--limit", type=int, default=5, help="nombre de groupes affichés")
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
