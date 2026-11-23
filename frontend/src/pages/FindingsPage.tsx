import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Search, X } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api } from "../api/client";
import type { DecisionValue, Finding, FindingDetail, FindingStatus, RecordRow, SchemaField, Severity } from "../api/types";
import { Button, Card, EmptyState, ErrorState, SEVERITIES, SeverityBadge, Skeleton } from "../components/ui";
import { CUSTOMER_FIELDS, CUSTOMER_KINDS, DECISIONS, FINDING_VIEWS, KIND_LABELS, STATUS_LABELS, isFindingView } from "../findings";
import { cn, formatNumber } from "../lib";

const PAGE_SIZE = 50;
const TECHNICAL = new Set(["record_id", "import_id", "source_file", "source_row", "excluded"]);

function StatusPill({ status }: { status: FindingStatus }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[13px] whitespace-nowrap", status === "open" ? "text-ink-2" : "text-success")}>
      <span aria-hidden className={cn("size-[7px] rounded-full", status === "open" ? "bg-ink-3" : "bg-success")} />
      {STATUS_LABELS[status]}
    </span>
  );
}

// --------------------------------------------------------------------------- filtres

function Filters({
  facets,
  severity,
  status,
  q,
  onChange,
}: {
  facets: Record<string, number> | undefined;
  severity: Severity | undefined;
  status: FindingStatus | undefined;
  q: string;
  onChange: (next: Record<string, string | undefined>) => void;
}) {
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);
  const searchId = useId();
  const chip = (active: boolean) =>
    cn(
      "inline-flex h-[30px] cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[13px] whitespace-nowrap",
      active ? "border-navy bg-navy text-white" : "border-line bg-surface text-ink-2 hover:bg-subtle",
    );
  return (
    <div className="flex flex-wrap items-center gap-2 px-5.5 py-3.5">
      <div role="group" aria-label="Filtrer par sévérité" className="flex flex-wrap gap-2">
        <button type="button" aria-pressed={!severity} className={chip(!severity)} onClick={() => onChange({ severity: undefined })}>
          Toutes
        </button>
        {(Object.keys(SEVERITIES) as Severity[]).map((s) => (
          <button key={s} type="button" aria-pressed={severity === s} className={chip(severity === s)} onClick={() => onChange({ severity: s })}>
            {SEVERITIES[s].label}
            <span className="tabular-nums opacity-70">{formatNumber(facets?.[s] ?? 0)}</span>
          </button>
        ))}
      </div>
      <select
        aria-label="Filtrer par statut"
        value={status ?? ""}
        onChange={(event) => onChange({ status: event.target.value || undefined })}
        className="ml-auto h-[30px] rounded-md border border-line bg-surface px-2 text-[13px]"
      >
        <option value="">Tous les statuts</option>
        {(Object.keys(STATUS_LABELS) as FindingStatus[]).map((s) => (
          <option key={s} value={s}>
            {STATUS_LABELS[s]}
          </option>
        ))}
      </select>
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          onChange({ q: text.trim() || undefined });
        }}
        className="flex h-[30px] items-center gap-1.5 rounded-md border border-line bg-surface px-2"
      >
        <Search aria-hidden className="size-3.5 text-ink-3" />
        <label htmlFor={searchId} className="sr-only">
          Rechercher un constat ou un enregistrement
        </label>
        <input
          id={searchId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Constat, enregistrement, texte…"
          className="w-56 bg-transparent text-[13px] outline-none"
        />
        {text ? (
          <button type="button" aria-label="Effacer la recherche" onClick={() => onChange({ q: undefined })} className="text-ink-3 hover:text-ink">
            <X aria-hidden className="size-3.5" />
          </button>
        ) : null}
      </form>
    </div>
  );
}

// --------------------------------------------------------------------------- détail

function recordsForDisplay(detail: FindingDetail): { rows: (RecordRow & { excluded: boolean })[]; grouped: boolean } {
  if (!CUSTOMER_KINDS.has(detail.kind)) return { rows: detail.records, grouped: false };
  // Une ligne par fiche client (fichier + identifiant client) plutôt qu'une par opération.
  const seen = new Map<string, RecordRow & { excluded: boolean; operations: number }>();
  for (const row of detail.records) {
    const key = `${String(row.import_id)}|${String(row.customer_id)}`;
    const current = seen.get(key);
    if (current) current.operations += 1;
    else seen.set(key, { ...row, operations: 1 });
  }
  return { rows: [...seen.values()], grouped: true };
}

function RecordsTable({ detail, schema }: { detail: FindingDetail; schema: SchemaField[] | undefined }) {
  const { rows, grouped } = recordsForDisplay(detail);
  const columns = useMemo(() => {
    const names = schema?.map((f) => f.name) ?? Object.keys(rows[0] ?? {}).filter((k) => !TECHNICAL.has(k));
    // Regroupé par fiche client : les colonnes propres à chaque opération n'ont pas de sens.
    return grouped ? names.filter((n) => CUSTOMER_FIELDS.has(n)) : names;
  }, [schema, rows, grouped]);
  const labels = new Map(schema?.map((f) => [f.name, f.label]));
  const differs = new Set(columns.filter((c) => new Set(rows.map((r) => String(r[c] ?? ""))).size > 1));

  return (
    <div>
      <p className="mb-2 text-xs text-ink-3">
        {grouped
          ? `${rows.length} fiches clients (${detail.records.length} opérations au total). `
          : `${rows.length} enregistrement(s). `}
        Les colonnes dont les valeurs diffèrent sont surlignées.
      </p>
      <div className="max-h-80 overflow-auto rounded-md border border-line">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0">
            <tr>
              <th scope="col" className="bg-subtle px-2.5 py-1.5 text-left font-medium whitespace-nowrap text-ink-2">
                Enregistrement
              </th>
              {columns.map((c) => (
                <th
                  key={c}
                  scope="col"
                  className={cn("px-2.5 py-1.5 text-left font-medium whitespace-nowrap", differs.has(c) ? "bg-warning-bg text-warning" : "bg-subtle text-ink-2")}
                >
                  {labels.get(c) ?? c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.record_id} className={cn("border-t border-line-subtle", row.excluded ? "text-ink-3 line-through" : "")}>
                <th scope="row" className="px-2.5 py-1.5 text-left font-mono font-normal whitespace-nowrap">
                  {row.record_id}
                  {"operations" in row ? <span className="ml-1.5 font-sans text-ink-3">· {String(row.operations)} op.</span> : null}
                </th>
                {columns.map((c) => (
                  <td key={c} className={cn("px-2.5 py-1.5 whitespace-nowrap", differs.has(c) ? "bg-warning-bg/50" : "")}>
                    {row[c] === null || row[c] === undefined || row[c] === "" ? <span className="text-ink-3">—</span> : String(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DecisionForm({ sessionId, finding }: { sessionId: string; finding: Finding }) {
  const queryClient = useQueryClient();
  const [decision, setDecision] = useState<DecisionValue>("confirmed");
  const [reason, setReason] = useState("");
  const reasonId = useId();
  const helpId = useId();
  const decide = useMutation({
    mutationFn: () => api.decide(sessionId, finding.finding_id, decision, reason.trim()),
    onSuccess: async () => {
      setReason("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["findings", sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["finding", sessionId, finding.finding_id] }),
        queryClient.invalidateQueries({ queryKey: ["session", sessionId] }),
      ]);
    },
  });
  const valid = reason.trim().length >= 5;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) decide.mutate();
      }}
      className="grid gap-3"
    >
      <fieldset className="grid min-w-0 grid-cols-2 gap-2">
        <legend className="mb-2 text-sm font-semibold">Décision</legend>
        {DECISIONS.map((d) => (
          <label
            key={d.value}
            className={cn(
              "flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-[13px] font-medium",
              decision === d.value ? "border-primary bg-brand-50 shadow-[inset_0_0_0_1px_var(--primary)]" : "border-line hover:border-line-strong",
            )}
          >
            <input type="radio" name="decision" checked={decision === d.value} onChange={() => setDecision(d.value)} className="mt-0.5 accent-[var(--primary)]" />
            <span>
              {d.label}
              <span className="block text-xs font-normal text-ink-3">{d.help}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <label htmlFor={reasonId} className="text-sm font-semibold">
        Motif
      </label>
      <textarea
        id={reasonId}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        aria-describedby={helpId}
        rows={3}
        className="rounded-md border border-line px-3 py-2 text-[13px]"
      />
      <span id={helpId} className="-mt-1.5 text-xs text-ink-3">
        Obligatoire (5 caractères au moins). Inscrit au journal d'audit avec la date et l'auteur ; la décision reste annulable.
      </span>
      {decide.isError ? <ErrorState error={decide.error} /> : null}
      {decide.isSuccess ? (
        <p role="status" className="flex items-center gap-2 rounded-md bg-success-bg px-3 py-2 text-[13px] text-success">
          <CheckCircle2 aria-hidden className="size-4" />
          Décision {decide.data.target} enregistrée. Une décision ne modifie pas les données.
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={!valid || decide.isPending}>
          {decide.isPending ? "Enregistrement…" : "Enregistrer la décision"}
        </Button>
      </div>
    </form>
  );
}

function DetailPanel({
  sessionId,
  findingId,
  schema,
  onClose,
}: {
  sessionId: string;
  findingId: string;
  schema: SchemaField[] | undefined;
  onClose: () => void;
}) {
  const detail = useQuery({ queryKey: ["finding", sessionId, findingId], queryFn: () => api.finding(sessionId, findingId) });
  return (
    <Card className="xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto">
      <header className="flex items-start gap-3 border-b border-line-subtle px-5 py-4">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs text-ink-3">{findingId}</p>
          {detail.data ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <SeverityBadge severity={detail.data.severity} />
              <span className="text-[13px] text-ink-2">{KIND_LABELS[detail.data.kind] ?? detail.data.kind}</span>
              <StatusPill status={detail.data.status} />
            </div>
          ) : null}
        </div>
        <button type="button" aria-label="Fermer le détail" onClick={onClose} className="grid size-8 cursor-pointer place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-ink">
          <X aria-hidden className="size-4" />
        </button>
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 px-5 py-4">
        {detail.isPending ? <Skeleton className="h-48" /> : null}
        {detail.isError ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : null}
        {detail.data ? (
          <>
            <p className="text-[14px] leading-relaxed">{detail.data.explanation}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
              <dt className="text-ink-3">Règle ou méthode</dt>
              <dd className="font-mono text-xs">{detail.data.rule_id}</dd>
              {detail.data.threshold ? (
                <>
                  <dt className="text-ink-3">Seuil</dt>
                  <dd>{detail.data.threshold}</dd>
                </>
              ) : null}
            </dl>
            <p className="rounded-md bg-app px-3 py-2.5 text-[13px] text-ink-2">
              <b className="font-medium text-ink">Recommandation : </b>
              {detail.data.recommendation}
            </p>
            <RecordsTable detail={detail.data} schema={schema} />
            <DecisionForm key={detail.data.finding_id} sessionId={sessionId} finding={detail.data} />
          </>
        ) : null}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- page

export function FindingsPage() {
  const { sessionId = "", view: rawView } = useParams();
  const view = isFindingView(rawView) ? rawView : "all";
  const [params, setParams] = useSearchParams();
  const severity = (params.get("severity") as Severity | null) ?? undefined;
  const status = (params.get("status") as FindingStatus | null) ?? undefined;
  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const selected = params.get("f");

  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  const query = { kind: FINDING_VIEWS[view].kinds, severity, status, q: q || undefined, page, page_size: PAGE_SIZE };
  const findings = useQuery({
    queryKey: ["findings", sessionId, query],
    queryFn: () => api.findings(sessionId, query),
    enabled: Boolean(session.data?.analysis),
    placeholderData: keepPreviousData,
  });

  function update(next: Record<string, string | undefined>, keepPage = false) {
    const copy = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined) copy.delete(key);
      else copy.set(key, value);
    }
    if (!keepPage && !("page" in next)) copy.delete("page");
    setParams(copy, { replace: true });
  }

  const title = FINDING_VIEWS[view].label;
  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;
  if (session.data && !session.data.analysis) {
    return (
      <Card>
        <EmptyState icon={CircleAlert} title="Aucune analyse pour cette session" action={<Link to={`/sessions/${sessionId}`} className="font-medium text-primary hover:underline">Aller à la vue d'ensemble</Link>}>
          Lancez l'analyse depuis la vue d'ensemble pour obtenir les constats.
        </EmptyState>
      </Card>
    );
  }

  const data = findings.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1.5 text-ink-2">
          Chaque constat attend une décision motivée. Une alerte ou une valeur atypique n'est pas une erreur ; aucun constat ne
          qualifie une opération de fraude.
        </p>
      </div>
      {session.data && !session.data.analysis_up_to_date ? (
        <div role="status" className="mb-4 flex items-center gap-2 rounded-lg bg-warning-bg px-4 py-2.5 text-[13px] text-warning">
          <CircleAlert aria-hidden className="size-4" />
          Des corrections ont été faites depuis l'analyse : relancez-la depuis la vue d'ensemble pour des constats à jour.
        </div>
      ) : null}

      <div className={cn("grid items-start gap-5", selected ? "grid-cols-[minmax(0,1fr)] xl:grid-cols-[minmax(0,1fr)_minmax(420px,520px)]" : "grid-cols-[minmax(0,1fr)]")}>
        <Card>
          <Filters facets={data?.facets.severity} severity={severity} status={status} q={q} onChange={update} />
          {findings.isError ? (
            <div className="px-5.5 pb-5">
              <ErrorState error={findings.error} onRetry={() => void findings.refetch()} />
            </div>
          ) : null}
          {!data ? (
            <div className="grid gap-2 px-5.5 pb-5" aria-busy="true" aria-label="Chargement des constats">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          ) : data.total === 0 ? (
            <EmptyState icon={CheckCircle2} title="Aucun constat pour ces filtres">
              Modifiez ou retirez les filtres pour voir d'autres constats.
            </EmptyState>
          ) : (
            <>
              <div className={cn("overflow-x-auto transition-opacity", findings.isFetching ? "opacity-60" : "")}>
                <table className="w-full border-collapse text-[13px]">
                  <thead>
                    <tr className="text-left text-xs text-ink-3">
                      <th scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] px-4 pl-5.5 font-medium">Constat</th>
                      <th scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] px-4 font-medium">Sévérité</th>
                      <th scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] px-4 font-medium">Type</th>
                      <th scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] px-4 pr-5.5 font-medium">Statut</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((f) => (
                      <tr
                        key={f.finding_id}
                        aria-selected={selected === f.finding_id}
                        onClick={() => update({ f: f.finding_id }, true)}
                        className={cn(
                          "cursor-pointer border-b border-line-subtle transition-colors hover:bg-[#f8fafc]",
                          selected === f.finding_id ? "bg-brand-50 shadow-[inset_2px_0_0_var(--primary)]" : "",
                        )}
                      >
                        <td className="w-full max-w-0 px-4 py-2.5 pl-5.5">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              update({ f: f.finding_id }, true);
                            }}
                            className="block w-full cursor-pointer truncate text-left font-medium hover:text-primary"
                            title={f.explanation}
                          >
                            {f.explanation}
                          </button>
                          <span className="font-mono text-xs text-ink-3">
                            {f.finding_id} · {f.record_ids.length} enreg.
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <SeverityBadge severity={f.severity} />
                        </td>
                        <td className="px-4 py-2.5 whitespace-nowrap text-ink-2">{KIND_LABELS[f.kind] ?? f.kind}</td>
                        <td className="px-4 py-2.5 pr-5.5">
                          <StatusPill status={f.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <nav aria-label="Pagination" className="flex items-center gap-3 px-5.5 py-3 text-[13px] text-ink-3">
                <span>
                  {formatNumber((page - 1) * PAGE_SIZE + 1)}–{formatNumber(Math.min(page * PAGE_SIZE, data.total))} sur {formatNumber(data.total)}
                </span>
                <div className="ml-auto flex gap-2">
                  <Button className="h-8" icon={ChevronLeft} disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>
                    Précédent
                  </Button>
                  <Button className="h-8" disabled={page >= pages} onClick={() => update({ page: String(page + 1) })}>
                    Suivant
                    <ChevronRight aria-hidden className="size-4" />
                  </Button>
                </div>
              </nav>
            </>
          )}
        </Card>

        {selected ? (
          <DetailPanel sessionId={sessionId} findingId={selected} schema={session.data?.schema} onClose={() => update({ f: undefined }, true)} />
        ) : null}
      </div>
    </>
  );
}
