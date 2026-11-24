"""Rapport qualité HTML autonome : un seul fichier, sans dépendance ni script, imprimable.

Toutes les valeurs issues des données sont échappées (`html.escape`) : un nom de client
contenant du HTML ne peut pas s'exécuter dans le rapport.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from sidadr.analysis import load_findings, load_summary
from sidadr.correction import CorrectionService
from sidadr.findings import Severity
from sidadr.reporting.traceability import build_traceability, traceability_rows

_SEVERITY_CLASS = {
    Severity.CERTAIN_ERROR.value: "certain",
    Severity.PROBABLE_INCONSISTENCY.value: "probable",
    Severity.ATYPICAL_VALUE.value: "atypical",
    Severity.REVIEW_ALERT.value: "review",
}
_STATUS = {
    "open": "Ouvert",
    "confirmed": "Confirmé",
    "rejected": "Rejeté",
    "kept_both": "Conservés",
    "deferred": "Différé",
}
_KINDS = {
    "rule_violation": "Règle métier",
    "exact_duplicate_row": "Lignes identiques",
    "duplicate_key": "Identifiant en double",
    "cross_file_duplicate": "Doublon inter-fichiers",
    "combination_match": "Combinaison identique",
    "atypical_value": "Valeur atypique",
    "name_match": "Noms similaires",
    "multi_bank": "Client multi-banques",
    "multi_branch": "Client multi-agences",
}

CSS = """
:root{--navy:#0b1b33;--primary:#1f4e8c;--bg:#f7f8fa;--surface:#fff;--border:#e6e9ee;--subtle:#eef1f4;
--text:#0f1b2d;--text2:#4a5768;--text3:#5f6b7a;--blue:#2d63ab;
--c-fg:#a8201a;--c-bg:#fdebe9;--p-fg:#8a4f00;--p-bg:#fff2df;--a-fg:#1f5fa8;--a-bg:#e6eef8;--r-fg:#4c3fa0;--r-bg:#efedfa;
--ok-fg:#1e6b45;--ok-bg:#e6f3ec}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 24px 64px}
header.top{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.mark{width:32px;height:32px;border-radius:8px;background:var(--navy);color:#fff;display:grid;place-items:center;font-weight:700;font-size:13px}
h1{font-size:24px;letter-spacing:-.02em;margin:0}h2{font-size:16px;margin:0 0 12px}
.meta{color:var(--text2);margin:0 0 24px}.muted{color:var(--text3);font-size:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:240px 1fr;gap:28px;align-items:center}
.score{font-size:44px;font-weight:600;letter-spacing:-.03em;font-variant-numeric:tabular-nums}.score small{font-size:16px;color:var(--text3);font-weight:400}
.dim{display:grid;grid-template-columns:110px 1fr 330px;gap:12px;align-items:center;margin:8px 0;font-size:13px}
.bar{height:8px;background:var(--subtle);border-radius:99px;overflow:hidden}.bar span{display:block;height:100%;background:var(--blue)}
.num{text-align:right;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--text3);font-weight:500;font-size:12px;padding:8px 10px;border-bottom:1px solid var(--border);background:#fcfcfd}
td{padding:8px 10px;border-bottom:1px solid var(--subtle);vertical-align:top}tr:last-child td{border-bottom:0}
.badge{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:500;white-space:nowrap}
.certain{color:var(--c-fg);background:var(--c-bg)}.probable{color:var(--p-fg);background:var(--p-bg)}
.atypical{color:var(--a-fg);background:var(--a-bg)}.review{color:var(--r-fg);background:var(--r-bg)}
.ok{color:var(--ok-fg);background:var(--ok-bg)}.warn{color:var(--p-fg);background:var(--p-bg)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.kpi{border:1px solid var(--border);border-radius:10px;padding:14px}
.kpi b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.note{background:var(--bg);border-radius:8px;padding:10px 12px;color:var(--text2);font-size:12px;margin-top:12px}
code{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;background:var(--bg);padding:1px 5px;border-radius:4px;word-break:break-all}
td:first-child code{white-space:nowrap;word-break:normal}
@media (max-width:760px){.grid,.dim{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr}}
@media print{body{background:#fff}.card{break-inside:avoid;border-color:#ccc}}
"""


def _e(value: object) -> str:
    return escape("" if value is None else str(value))


def _badge(severity: str) -> str:
    return f'<span class="badge {_SEVERITY_CLASS.get(severity, "")}">{_e(Severity(severity).label)}</span>'


def _score_section(summary: dict[str, Any]) -> str:
    score = summary["score"]
    dims = []
    for d in score["dimensions"]:
        if d["value"] is None:
            dims.append(
                f'<div class="dim"><span>{_e(d["label"])}</span><span class="muted">non évaluée</span><span></span></div>'
            )
            continue
        pct = 100 * d["value"]
        dims.append(
            f'<div class="dim"><span>{_e(d["label"])}</span><div class="bar"><span style="width:{pct:.1f}%"></span></div>'
            f'<span class="num">{pct:.1f} % <span class="muted">· {d["numerator"]} en défaut sur {d["denominator"]} · poids {d["effective_weight"]:.2f}</span></span></div>'
        )
    notes = "".join(f"<div>{_e(n)}</div>" for n in score["notes"])
    return (
        f'<section class="card"><h2>Score qualité</h2><div class="grid"><div><div class="score">{score["score"]:.1f}<small> / 100</small></div>'
        f'<div class="muted">sur {summary["rows_analyzed"]} enregistrements analysés</div></div><div>{"".join(dims)}</div></div>'
        f'<div class="note">{notes}</div></section>'
    )


def _diverse(findings: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Exemples variés : un constat de chaque règle ou type à tour de rôle, plutôt que
    les dix premiers d'une même règle."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in findings:
        groups.setdefault((f["kind"], f["rule_id"]), []).append(f)
    picked: list[dict[str, Any]] = []
    depth = 0
    while len(picked) < limit and any(len(g) > depth for g in groups.values()):
        picked += [g[depth] for g in groups.values() if len(g) > depth][: limit - len(picked)]
        depth += 1
    return picked


def _findings_section(
    findings: list[dict[str, Any]], decisions: dict[str, str], per_severity: int
) -> str:
    for f in findings:
        f["status"] = decisions.get(f["key"], "open")
    counts = Counter(f["severity"] for f in findings)
    kpis = "".join(
        f'<div class="kpi">{_badge(s.value)}<b>{counts.get(s.value, 0)}</b></div>' for s in Severity
    )
    by_kind = Counter((f["kind"], f["status"]) for f in findings)
    kinds = sorted({k for k, _ in by_kind})
    statuses = [s for s in _STATUS if any(by_kind.get((k, s)) for k in kinds)]
    head = "".join(f'<th class="num">{_e(_STATUS[s])}</th>' for s in statuses)
    rows = "".join(
        f"<tr><td>{_e(_KINDS.get(k, k))}</td>"
        + "".join(f'<td class="num">{by_kind.get((k, s), 0)}</td>' for s in statuses)
        + f'<td class="num"><b>{sum(by_kind.get((k, s), 0) for s in statuses)}</b></td></tr>'
        for k in kinds
    )
    detail = []
    for severity in Severity:
        selected = _diverse([f for f in findings if f["severity"] == severity.value], per_severity)
        if not selected:
            continue
        total = counts.get(severity.value, 0)
        lines = "".join(
            f"<tr><td><code>{_e(f['finding_id'])}</code></td><td>{_e(_KINDS.get(f['kind'], f['kind']))}</td>"
            f"<td>{_e(f['explanation'])}</td><td>{_e(_STATUS.get(f['status'], f['status']))}</td></tr>"
            for f in selected
        )
        detail.append(
            f'<h2 style="margin-top:20px">{_badge(severity.value)} <span class="muted">{len(selected)} exemples sur {total}</span></h2>'
            f"<table><thead><tr><th>Constat</th><th>Type</th><th>Explication</th><th>Statut</th></tr></thead><tbody>{lines}</tbody></table>"
        )
    return (
        f'<section class="card"><h2>Constats ({len(findings)})</h2><div class="kpis">{kpis}</div>'
        f'<table style="margin-top:16px"><thead><tr><th>Type</th>{head}<th class="num">Total</th></tr></thead><tbody>{rows}</tbody></table>'
        f'<div class="note">Une alerte à examiner ou une valeur atypique n\'est pas une erreur : elle demande une vérification humaine. '
        f"Aucun constat ne qualifie une opération de fraude.</div></section>"
        f'<section class="card"><h2>Exemples de constats</h2>{"".join(detail)}</section>'
    )


def _rules_section(summary: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{_e(r['rule_id'])}</code></td><td>{_e(r['description'])}</td><td>{_badge(r['severity'])}</td>"
        f'<td>{_e(r["dimension"])}</td><td class="num">{r["checked"]}</td><td class="num">{r["violations"]}</td><td class="num">{r["not_evaluable"]}</td></tr>'
        for r in summary.get("rules", [])
    )
    return (
        '<section class="card"><h2>Règles métier appliquées</h2><table><thead><tr><th>Règle</th><th>Description</th><th>Sévérité</th>'
        f'<th>Dimension</th><th class="num">Vérifiées</th><th class="num">Violations</th><th class="num">Non évaluables</th></tr></thead><tbody>{rows}</tbody></table></section>'
    )


def _journal_section(service: CorrectionService, limit: int) -> str:
    entries = service.history()
    check = service.verify_journal()
    status = (
        f'<span class="badge ok">Chaîne d\'empreintes intacte ({check.entries} entrées)</span>'
        if check.valid
        else f'<span class="badge certain">Journal altéré à l\'entrée #{check.first_broken_entry}</span>'
    )
    actions = Counter(e.action_type for e in entries)
    summary = ", ".join(f"{n} {a}" for a, n in sorted(actions.items())) or "aucune action"
    rows = "".join(
        f"<tr><td>#{e.entry_id}</td><td>{_e(e.ts_utc)}</td><td>{_e(e.actor)}</td><td>{_e(e.action_type)}</td>"
        f"<td>{_e(e.target)} {_e(e.finding_id or '')} {_e(e.record_id or '')}{'.' + _e(e.field) if e.field else ''}</td>"
        f"<td>{_e(e.old_value)} → {_e(e.new_value)}</td><td>{_e(e.reason)}</td></tr>"
        for e in entries[-limit:]
    )
    table = (
        f"<table><thead><tr><th>#</th><th>Horodatage UTC</th><th>Auteur</th><th>Action</th><th>Cible</th><th>Valeurs</th><th>Motif</th></tr></thead><tbody>{rows}</tbody></table>"
        if entries
        else '<p class="muted">Aucune correction ni décision.</p>'
    )
    return f'<section class="card"><h2>Corrections et décisions</h2><p>{status} &nbsp; {_e(summary)}</p>{table}</section>'


def render_report(
    service: CorrectionService, per_severity: int = 10, journal_limit: int = 30
) -> str:
    summary = load_summary(service.store.summary_path)
    if summary is None:
        raise ValueError(
            f"Aucune analyse enregistrée : lancez d'abord « sidadr analyze {service.store.session_id} »."
        )
    findings = load_findings(service.store.findings_path)
    trace = build_traceability(service, summary)
    stale = (
        ""
        if trace["analysis_up_to_date"]
        else (
            '<div class="note" style="background:var(--p-bg);color:var(--p-fg)">L\'analyse est antérieure aux dernières '
            "corrections ou exclusions : relancez « sidadr analyze » pour un rapport à jour.</div>"
        )
    )
    sources = ", ".join(f"{s['source_file']} ({s['rows']} lignes)" for s in trace["sources"])
    trace_rows = "".join(
        f"<tr><td>{_e(k)}</td><td><code>{_e(v)}</code></td></tr>"
        for k, v in traceability_rows(trace)
    )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport qualité SIDADR — {_e(trace["session_name"])}</title><style>{CSS}</style></head>
<body><main>
<header class="top"><div class="mark">SD</div><div><h1>Rapport qualité des données</h1></div></header>
<p class="meta">Session <code>{_e(trace["session_id"])}</code> · {_e(trace["session_name"])} · analyse du {_e(summary["generated_at"])} · SIDADR {_e(trace["sidadr_version"])}<br>
Sources : {_e(sources)} · {trace["working_rows"]} enregistrements de travail ({trace["excluded_records"]} exclus, {trace["active_corrections"]} corrections actives)</p>
{stale}
{_score_section(summary)}
{_findings_section(findings, service.decisions(), per_severity)}
{_rules_section(summary)}
{_journal_section(service, journal_limit)}
<section class="card"><h2>Traçabilité</h2><table><tbody>{trace_rows}</tbody></table></section>
<p class="muted">Rapport généré le {_e(trace["generated_at"])} par SIDADR. Prototype : les seuils ont été calibrés sur des données synthétiques.</p>
</main></body></html>
"""


def write_report(service: CorrectionService, path: Path, per_severity: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(service, per_severity), encoding="utf-8")
    return path
