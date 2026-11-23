"""Commandes de session : enregistrer, analyser, décider, corriger, annuler, consulter le journal.

sidadr session-create FICHIERS --accept-proposed [--name NOM]
sidadr sessions
sidadr analyze SESSION
sidadr findings SESSION [--status open] [--kind name_match] [--limit 20]
sidadr decide SESSION ID_CONSTAT {confirmed,rejected,kept_both,deferred} --reason "..."
sidadr correct SESSION ID_ENREGISTREMENT CHAMP VALEUR --reason "..."
sidadr exclude SESSION ID_ENREGISTREMENT --reason "..."
sidadr undo SESSION C12|D3 --reason "..."
sidadr history SESSION [--verify]
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sidadr.analysis import (
    analyze,
    findings_to_records,
    load_findings,
    save_findings,
    save_summary,
    summarize,
)
from sidadr.correction import CorrectionService, Decision
from sidadr.export import export_session
from sidadr.findings import Severity
from sidadr.ingestion import ImportSession
from sidadr.ingestion.generic import GenericSchema, infer_generic_schema, suggest_mode
from sidadr.reporting.html_report import write_report
from sidadr.storage import SessionStore, Workspace, default_workspace

EXIT_OK, EXIT_ERROR, EXIT_NEEDS_CONFIRMATION = 0, 1, 2


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace(args.workspace or default_workspace())


def _service(args: argparse.Namespace) -> CorrectionService:
    return CorrectionService(_workspace(args).open_session(args.session), actor=args.actor)


def print_generic_schema(generic: GenericSchema) -> None:
    print("\n  Schéma déduit des fichiers (mode générique) :")
    for f in generic.fields:
        spec = generic.schema[f.name]
        flag = "  [obligatoire]" if spec.required else ""
        print(
            f"    {f.label:<26} -> {f.name:<24} {f.type:<8} {f.filled:>7} valeurs  ({f.reason}){flag}"
        )


def _print_created(store: SessionStore) -> None:
    print(f"\nSession créée : {store.session_id}  (mode {store.mode})")
    print(f"  {store.metadata['rows']} enregistrements ; instantané original en lecture seule")
    print(f"  empreinte SHA-256 : {store.metadata['original_sha256'][:16]}…")
    if store.options.get("key_fields"):
        print(f"  clé d'unicité : {', '.join(store.options['key_fields'])}")
    print(f"  dossier : {store.directory}")
    print(f"\nÉtape suivante : sidadr analyze {store.session_id}")


def cmd_session_create(
    args: argparse.Namespace, load_session: Callable[[argparse.Namespace], Any]
) -> int:
    probe = ImportSession()
    inspections = [probe.inspect(path, args.sheet) for path in args.files]
    missing = {i.source_file: i.mapping.missing_required(probe.schema) for i in inspections}
    mode = suggest_mode(missing) if args.mode == "auto" else args.mode

    if mode == "banking":
        if args.key or args.group_by:
            print(
                "Note : --key et --group-by ne s'appliquent qu'au mode générique ; le mode bancaire utilise sa configuration."
            )
        session = load_session(args)
        if session is None:
            return EXIT_NEEDS_CONFIRMATION
        store = _workspace(args).create_session(session, args.name, "banking")
        _print_created(store)
        return EXIT_OK

    if args.mode == "auto":
        absent = sorted({f for fields in missing.values() for f in fields})
        print(
            f"Mode générique retenu : ces fichiers ne contiennent pas tous les champs bancaires ({', '.join(absent)})."
        )
    generic = infer_generic_schema([i.raw for i in inspections]).with_options(args.required)
    key_fields = [generic.resolve(k) for k in args.key]
    group_by = [generic.resolve(g) for g in args.group_by]
    if not args.accept_proposed:
        print_generic_schema(generic)
        print("\nLe schéma déduit doit être confirmé : relancez avec --accept-proposed.")
        print(
            "Options : --key COLONNE (clé d'unicité), --required COLONNE, --group-by COLONNE, --rules FICHIER.yaml"
        )
        return EXIT_NEEDS_CONFIRMATION
    session = ImportSession(schema=generic.schema)
    for inspection in inspections:
        session.add(inspection, generic.mappings[inspection.source_file].confirm(generic.schema))
    options: dict[str, Any] = {"key_fields": key_fields, "group_by": group_by}
    if args.rules:
        options["rules_path"] = str(Path(args.rules).resolve())
    store = _workspace(args).create_session(session, args.name, "generic", options)
    print_generic_schema(generic)
    _print_created(store)
    return EXIT_OK


def cmd_sessions(args: argparse.Namespace) -> int:
    sessions = _workspace(args).list_sessions()
    if not sessions:
        print(
            "Aucune session. Créez-en une avec : sidadr session-create FICHIERS --accept-proposed"
        )
        return EXIT_OK
    for s in sessions:
        print(f"{s['session_id']}  {s['created_at']}  {s['rows']:>7} enreg.  {s['name']}")
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    service = _service(args)
    frame = service.working_frame()
    overrides = {"reference_date": args.reference_date} if args.reference_date else None
    store = service.store
    result = analyze(
        frame,
        service.schema,
        rules_path=args.rules,
        rule_overrides=overrides,
        mode=store.mode,
        options=store.options,
    )
    records = findings_to_records(result.findings(), service.decisions())
    save_findings(service.store.findings_path, records)
    save_summary(service.store.summary_path, summarize(result, records, len(service.history())))

    excluded = len(service.excluded_records())
    corrections = len(service.active_corrections())
    print(f"\n=== Analyse de {service.store.session_id} : {len(frame)} enregistrements de travail")
    print(f"  {corrections} correction(s) active(s), {excluded} enregistrement(s) exclu(s)")
    print(f"\n  Score qualité : {result.score.score:.1f} / 100")
    for d in result.score.dimensions:
        value = (
            "non évaluée"
            if d.value is None
            else f"{100 * d.value:5.1f} %  ({d.numerator} en défaut sur {d.denominator})"
        )
        print(f"    {d.label:<12} {value}")

    print(f"\n  {len(records)} constats :")
    by_severity = Counter(r["severity"] for r in records)
    for severity in Severity:
        print(f"    {severity.label:<24} {by_severity.get(severity.value, 0):>6}")
    by_kind = Counter(r["kind"] for r in records)
    print("  Par type : " + ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items())))
    by_status = Counter(r["status"] for r in records)
    print("  Par statut : " + ", ".join(f"{k} {n}" for k, n in sorted(by_status.items())))
    print(f"\nConstats enregistrés : {service.store.findings_path}")
    print(f"Consulter : sidadr findings {service.store.session_id} --status open")
    return EXIT_OK


def cmd_findings(args: argparse.Namespace) -> int:
    service = _service(args)
    records = load_findings(service.store.findings_path)
    if not records:
        print(f"Aucune analyse enregistrée. Lancez : sidadr analyze {args.session}")
        return EXIT_ERROR
    decisions = service.decisions()
    selected = []
    for r in records:
        r["status"] = decisions.get(r["key"], "open")
        if args.status and r["status"] != args.status:
            continue
        if args.kind and r["kind"] != args.kind:
            continue
        if args.severity and r["severity"] != args.severity:
            continue
        selected.append(r)
    selected.sort(key=lambda r: (Severity(r["severity"]).rank, r["finding_id"]))
    print(f"{len(selected)} constat(s) sur {len(records)}")
    for r in selected[: args.limit]:
        records_text = ", ".join(r["record_ids"][:4]) + (" …" if len(r["record_ids"]) > 4 else "")
        print(
            f"\n  {r['finding_id']}  [{Severity(r['severity']).label}]  {r['kind']}  statut : {r['status']}"
        )
        print(f"      {r['explanation']}")
        print(f"      enregistrements : {records_text}")
    return EXIT_OK


def cmd_decide(args: argparse.Namespace) -> int:
    service = _service(args)
    records = {r["finding_id"]: r for r in load_findings(service.store.findings_path)}
    if args.finding_id not in records:
        print(
            f"Constat {args.finding_id} absent de la dernière analyse. Relancez : sidadr analyze {args.session}"
        )
        return EXIT_ERROR
    record = records[args.finding_id]
    decision_id = service.decide(
        record["key"], args.finding_id, Decision(args.decision), args.reason
    )
    print(f"Décision D{decision_id} enregistrée : {args.finding_id} -> {args.decision}")
    print(f"  {record['explanation']}")
    print(f'  Annulable avec : sidadr undo {args.session} D{decision_id} --reason "..."')
    print("  Aucun enregistrement n'a été modifié : une décision ne corrige pas les données.")
    return EXIT_OK


def cmd_correct(args: argparse.Namespace) -> int:
    service = _service(args)
    before = service.current_value(args.record_id, args.field)
    correction_id = service.correct(args.record_id, args.field, args.value, args.reason)
    after = service.current_value(args.record_id, args.field)
    print(f"Correction C{correction_id} enregistrée sur {args.record_id}")
    print(
        f"  {args.field} : {before if before is not None else '(vide)'} -> {after if after is not None else '(vide)'}"
    )
    print("  L'original est inchangé ; la vue de travail utilise la nouvelle valeur.")
    print(f'  Annulable avec : sidadr undo {args.session} C{correction_id} --reason "..."')
    return EXIT_OK


def cmd_exclude(args: argparse.Namespace) -> int:
    service = _service(args)
    correction_id = service.exclude(args.record_id, args.reason)
    print(
        f"Exclusion C{correction_id} : {args.record_id} est écarté de la vue de travail (non supprimé)."
    )
    print(f'  Annulable avec : sidadr undo {args.session} C{correction_id} --reason "..."')
    return EXIT_OK


def cmd_undo(args: argparse.Namespace) -> int:
    service = _service(args)
    service.undo(args.target, args.reason)
    print(f"{args.target.upper()} annulé. L'annulation est inscrite au journal.")
    return EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    service = _service(args)
    entries = service.history()
    print(f"Journal de {args.session} : {len(entries)} entrée(s)")
    for e in entries[-args.limit :]:
        target = f"{e.target} " if e.target else ""
        where = (
            f"{e.record_id}.{e.field} "
            if e.record_id and e.field
            else (f"{e.record_id} " if e.record_id else "")
        )
        change = (
            f"{_shown(e.old_value)} -> {_shown(e.new_value)} "
            if e.old_value is not None or e.new_value is not None
            else ""
        )
        finding = f"[{e.finding_id}] " if e.finding_id else ""
        print(
            f"  #{e.entry_id:<4} {e.ts_utc}  {e.actor:<12} {e.action_type:<8} {target}{finding}{where}{change}| {e.reason}"
        )
    if args.verify:
        check = service.verify_journal()
        if check.valid:
            print(
                f"\nIntégrité du journal vérifiée : chaîne d'empreintes intacte ({check.entries} entrées)."
            )
        else:
            print(
                f"\nJOURNAL ALTÉRÉ : la chaîne d'empreintes est rompue à l'entrée #{check.first_broken_entry}."
            )
            return EXIT_ERROR
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    service = _service(args)
    out = args.out or Path("exports") / service.store.session_id
    result = export_session(service, out, args.format)
    print(f"Export {args.format.upper()} de {service.store.session_id} :")
    for path in result.files:
        print(f"  {path}")
    trace = result.traceability
    print(
        f"  {trace['working_rows']} enregistrements de travail, {trace['active_corrections']} corrections, {trace['excluded_records']} exclus"
    )
    if result.neutralized_cells:
        print(
            f"  {result.neutralized_cells} cellule(s) neutralisée(s) contre l'injection de formules (préfixe « ' »)"
        )
    if not trace["analysis_up_to_date"]:
        print(
            f"  Attention : l'analyse est antérieure aux dernières actions. Relancez : sidadr analyze {args.session}"
        )
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    service = _service(args)
    out = args.out or Path("exports") / f"rapport_{service.store.session_id}.html"
    try:
        path = write_report(service, out, args.examples)
    except ValueError as exc:
        print(exc)
        return EXIT_ERROR
    print(f"Rapport qualité : {path}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from sidadr.api import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "Attention : l'API n'a pas d'authentification. Ne l'exposez pas sur un réseau partagé."
        )
    root = args.workspace or default_workspace()
    print(
        f"API SIDADR sur http://{args.host}:{args.port}/api  (documentation : /api/docs, espace : {root})"
    )
    uvicorn.run(create_app(root), host=args.host, port=args.port, log_level="warning")
    return EXIT_OK


def _shown(value: str | None) -> str:
    return "(vide)" if value is None else value


def register(
    sub: Any,
    add_import_options: Callable[[argparse.ArgumentParser], None],
    load_session: Callable[[argparse.Namespace], Any],
) -> None:
    """Ajoute les sous-commandes de session au parseur principal."""

    def session_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        p: argparse.ArgumentParser = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--workspace",
            type=Path,
            help="dossier de travail (défaut : SIDADR_WORKSPACE ou ./workspace)",
        )
        p.add_argument("--actor", help="auteur des actions (défaut : utilisateur du système)")
        return p

    p = session_parser("session-create", "importer des fichiers et créer une session de travail")
    add_import_options(p)
    p.add_argument("--name", help="nom lisible de la session")
    p.add_argument(
        "--mode",
        choices=["auto", "banking", "generic"],
        default="auto",
        help="auto : bancaire si les fichiers couvrent le schéma bancaire, sinon générique",
    )
    p.add_argument(
        "--key", action="append", default=[], help="(générique) colonne identifiant, répétable"
    )
    p.add_argument(
        "--required", action="append", default=[], help="(générique) colonne obligatoire, répétable"
    )
    p.add_argument(
        "--group-by",
        action="append",
        default=[],
        help="(générique) colonne de regroupement des anomalies",
    )
    p.add_argument(
        "--rules", type=Path, help="(générique) règles YAML écrites sur les noms de colonnes"
    )
    p.set_defaults(func=lambda a: cmd_session_create(a, load_session))

    session_parser("sessions", "lister les sessions").set_defaults(func=cmd_sessions)

    p = session_parser("analyze", "analyser la vue de travail d'une session")
    p.add_argument("session")
    p.add_argument("--rules", type=Path, help="fichier de règles YAML")
    p.add_argument("--reference-date", help="date d'arrêté du contrôle, AAAA-MM-JJ")
    p.set_defaults(func=cmd_analyze)

    p = session_parser("findings", "consulter les constats de la dernière analyse")
    p.add_argument("session")
    p.add_argument("--status", choices=["open", *[d.value for d in Decision]])
    p.add_argument("--kind", help="type de constat (rule_violation, duplicate_key, name_match…)")
    p.add_argument("--severity", choices=[s.value for s in Severity])
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_findings)

    p = session_parser(
        "decide", "décider d'un constat (confirmer, rejeter, conserver les deux, différer)"
    )
    p.add_argument("session")
    p.add_argument("finding_id")
    p.add_argument("decision", choices=[d.value for d in Decision])
    p.add_argument("--reason", required=True, help="motif obligatoire")
    p.set_defaults(func=cmd_decide)

    p = session_parser("correct", "corriger une valeur (l'original reste intact)")
    p.add_argument("session")
    p.add_argument("record_id")
    p.add_argument("field")
    p.add_argument("value", help='nouvelle valeur ("" pour vider un champ facultatif)')
    p.add_argument("--reason", required=True, help="motif obligatoire")
    p.set_defaults(func=cmd_correct)

    p = session_parser("exclude", "écarter un enregistrement de la vue de travail")
    p.add_argument("session")
    p.add_argument("record_id")
    p.add_argument("--reason", required=True, help="motif obligatoire")
    p.set_defaults(func=cmd_exclude)

    p = session_parser("undo", "annuler une correction (C12) ou une décision (D3)")
    p.add_argument("session")
    p.add_argument("target")
    p.add_argument("--reason", required=True, help="motif obligatoire")
    p.set_defaults(func=cmd_undo)

    p = session_parser("export", "exporter données de travail, constats, journal et traçabilité")
    p.add_argument("session")
    p.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    p.add_argument("--out", type=Path, help="dossier de sortie (défaut : exports/<session>)")
    p.set_defaults(func=cmd_export)

    p = session_parser("report", "générer le rapport qualité HTML")
    p.add_argument("session")
    p.add_argument(
        "--out", type=Path, help="fichier HTML (défaut : exports/rapport_<session>.html)"
    )
    p.add_argument("--examples", type=int, default=10, help="exemples de constats par sévérité")
    p.set_defaults(func=cmd_report)

    p = session_parser("serve", "démarrer l'API locale utilisée par l'interface web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = session_parser("history", "consulter le journal d'audit")
    p.add_argument("session")
    p.add_argument("--verify", action="store_true", help="vérifier la chaîne d'empreintes")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_history)
