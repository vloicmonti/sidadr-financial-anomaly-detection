import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Ban, ChevronLeft, ChevronRight, Pencil, Search, X } from "lucide-react";
import { Fragment, useEffect, useId, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api } from "../api/client";
import type { Correction, RecordDetail, SchemaField } from "../api/types";
import { CellValue, MIN_REASON, ReasonField, StaleAnalysisBanner, UndoButton, useInvalidateSession } from "../components/session";
import { Button, Card, EmptyState, ErrorState, SeverityBadge, Skeleton } from "../components/ui";
import { KIND_LABELS, STATUS_LABELS } from "../findings";
import { cn, formatDateTime, formatNumber } from "../lib";

const PAGE_SIZE = 50;
const EXCLUDED = "__excluded__";

function asText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

// --------------------------------------------------------------------------- correction d'un champ

function FieldEditor({
  sessionId,
  recordId,
  field,
  current,
  onDone,
}: {
  sessionId: string;
  recordId: string;
  field: SchemaField;
  current: unknown;
  onDone: () => void;
}) {
  const [value, setValue] = useState(asText(current));
  const [reason, setReason] = useState("");
  const valueId = useId();
  const invalidate = useInvalidateSession(sessionId);
  const correct = useMutation({
    mutationFn: () => api.correct(sessionId, recordId, field.name, value.trim() === "" ? null : value, reason.trim()),
    onSuccess: async () => {
      await invalidate();
      onDone();
    },
  });
  const changed = value.trim() !== asText(current).trim();
  const hint =
    field.type === "date"
      ? "Date au format AAAA-MM-JJ ou JJ/MM/AAAA."
      : field.type === "decimal"
        ? "Nombre ; la virgule ou le point sont acceptés."
        : field.required
          ? "Champ obligatoire : il ne peut pas être vidé."
          : "Laisser vide pour effacer la valeur.";

  return (
    <form
      aria-label={`Corriger ${field.label}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (changed && reason.trim().length >= MIN_REASON) correct.mutate();
      }}
      className="grid gap-3 rounded-lg border border-primary/40 bg-brand-50/40 p-3"
    >
      <div className="grid gap-1.5">
        <label htmlFor={valueId} className="text-[13px] font-medium">
          Nouvelle valeur de « {field.label} »
        </label>
        {field.type === "enum" && field.values.length > 0 ? (
          <select id={valueId} value={value} onChange={(event) => setValue(event.target.value)} className="h-9 rounded-md border border-line bg-surface px-2 text-[13px]">
            {field.required ? null : <option value="">(vide)</option>}
            {field.values.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={valueId}
            value={value}
            inputMode={field.type === "decimal" ? "decimal" : undefined}
            placeholder={field.type === "date" ? "AAAA-MM-JJ ou JJ/MM/AAAA" : undefined}
            onChange={(event) => setValue(event.target.value)}
            className="h-9 rounded-md border border-line bg-surface px-3 text-[13px]"
          />
        )}
        <span className="text-xs text-ink-3">{hint} Le moteur vérifie la valeur avant de l'enregistrer.</span>
      </div>
      <ReasonField value={reason} onChange={setReason} />
      {correct.isError ? <ErrorState title="Correction refusée" error={correct.error} /> : null}
      <div className="flex justify-end gap-2">
        <Button className="h-8" onClick={onDone}>
          Fermer
        </Button>
        <Button type="submit" variant="primary" className="h-8" disabled={!changed || reason.trim().length < MIN_REASON || correct.isPending}>
          {correct.isPending ? "Enregistrement…" : "Enregistrer la correction"}
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------- exclusion

function ExclusionBox({ sessionId, detail }: { sessionId: string; detail: RecordDetail }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const invalidate = useInvalidateSession(sessionId);
  const exclude = useMutation({
    mutationFn: () => api.exclude(sessionId, detail.record_id, reason.trim()),
    onSuccess: async () => {
      setOpen(false);
      setReason("");
      await invalidate();
    },
  });
  const active = detail.corrections.find((c) => c.field === EXCLUDED && c.active === 1);

  if (detail.excluded && active) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-lg bg-subtle px-3 py-2.5 text-[13px]">
        <Ban aria-hidden className="size-4 text-ink-3" />
        <span className="flex-1">
          Exclu de la copie de travail (C{active.id}) : « {active.reason} ». L'enregistrement d'origine est conservé.
        </span>
        <UndoButton sessionId={sessionId} target={`C${active.id}`} label="Réintégrer" />
      </div>
    );
  }
  if (!open) {
    return (
      <div className="flex items-center gap-3 text-[13px] text-ink-2">
        <span className="flex-1">Exclure écarte l'enregistrement des analyses et des exports, sans le supprimer.</span>
        <Button className="h-8" icon={Ban} onClick={() => setOpen(true)}>
          Exclure
        </Button>
      </div>
    );
  }
  return (
    <form
      aria-label="Exclure l'enregistrement"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim().length >= MIN_REASON) exclude.mutate();
      }}
      className="grid gap-3 rounded-lg border border-line bg-app p-3"
    >
      <ReasonField value={reason} onChange={setReason} label="Motif de l'exclusion" />
      {exclude.isError ? <ErrorState title="Exclusion refusée" error={exclude.error} /> : null}
      <div className="flex justify-end gap-2">
        <Button className="h-8" onClick={() => setOpen(false)}>
          Fermer
        </Button>
        <Button type="submit" variant="primary" className="h-8" disabled={reason.trim().length < MIN_REASON || exclude.isPending}>
          {exclude.isPending ? "Exclusion…" : "Exclure l'enregistrement"}
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------- fiche

function CorrectionList({ sessionId, corrections, labels }: { sessionId: string; corrections: Correction[]; labels: Map<string, string> }) {
  if (corrections.length === 0) return <p className="text-[13px] text-ink-3">Aucune correction sur cet enregistrement.</p>;
  return (
    <ol className="grid grid-cols-[minmax(0,1fr)] gap-2">
      {[...corrections].reverse().map((c) => (
        <li key={c.id} className={cn("grid gap-1 rounded-lg border border-line-subtle px-3 py-2 text-[13px]", c.active ? "" : "opacity-60")}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-ink-3">C{c.id}</span>
            <b className="font-medium">{c.field === EXCLUDED ? "Exclusion" : labels.get(c.field) ?? c.field}</b>
            {c.field === EXCLUDED ? null : (
              <span className="text-ink-2">
                <CellValue value={c.old_value} /> → <CellValue value={c.new_value} />
              </span>
            )}
            <span className="ml-auto">
              {c.active ? <UndoButton sessionId={sessionId} target={`C${c.id}`} /> : <span className="text-xs text-ink-3">annulée</span>}
            </span>
          </div>
          <span className="text-xs text-ink-3">
            {formatDateTime(c.created_at)} · {c.actor} · « {c.reason} »
          </span>
        </li>
      ))}
    </ol>
  );
}

function RecordPanel({
  sessionId,
  recordId,
  schema,
  onClose,
}: {
  sessionId: string;
  recordId: string;
  schema: SchemaField[];
  onClose: () => void;
}) {
  const detail = useQuery({ queryKey: ["record", sessionId, recordId], queryFn: () => api.record(sessionId, recordId) });
  const [editing, setEditing] = useState<string | null>(null);
  useEffect(() => setEditing(null), [recordId]);
  const labels = new Map(schema.map((f) => [f.name, f.label]));
  const data = detail.data;

  return (
    <Card className="xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto">
      <header className="flex items-start gap-3 border-b border-line-subtle px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="font-mono text-[15px] font-semibold">{recordId}</h2>
          {data ? (
            <p className="mt-1 text-xs text-ink-3">
              {asText(data.original.source_file)} · ligne {asText(data.original.source_row)}
              {data.excluded ? " · exclu" : ""}
            </p>
          ) : null}
        </div>
        <button type="button" aria-label="Fermer la fiche" onClick={onClose} className="grid size-8 cursor-pointer place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-ink">
          <X aria-hidden className="size-4" />
        </button>
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 px-5 py-4">
        {detail.isPending ? <Skeleton className="h-64" /> : null}
        {detail.isError ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : null}
        {data ? (
          <>
            <section aria-label="Valeurs">
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr className="text-left text-xs text-ink-3">
                    <th scope="col" className="pb-1.5 font-medium">Champ</th>
                    <th scope="col" className="pb-1.5 font-medium">Origine</th>
                    <th scope="col" className="pb-1.5 font-medium">Travail</th>
                    <th scope="col" className="pb-1.5" aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {schema.map((field) => {
                    const original = data.original[field.name];
                    const working = data.working[field.name];
                    const changed = asText(original) !== asText(working);
                    return (
                      <Fragment key={field.name}>
                        <tr className="border-t border-line-subtle align-top">
                          <th scope="row" className="py-2 pr-3 text-left font-medium">
                            {field.label}
                            {field.required ? <span className="text-ink-3"> *</span> : null}
                          </th>
                          <td className={cn("py-2 pr-3 break-all", changed ? "text-ink-3 line-through" : "")}>
                            <CellValue value={original} />
                          </td>
                          <td className={cn("py-2 pr-3 break-all", changed ? "font-medium text-primary" : "")}>
                            <CellValue value={working} />
                          </td>
                          <td className="py-1.5 text-right">
                            {editing === field.name || data.excluded ? null : (
                              <button
                                type="button"
                                aria-label={`Corriger ${field.label}`}
                                title="Corriger"
                                onClick={() => setEditing(field.name)}
                                className="grid size-7 cursor-pointer place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-primary"
                              >
                                <Pencil aria-hidden className="size-3.5" />
                              </button>
                            )}
                          </td>
                        </tr>
                        {editing === field.name ? (
                          <tr>
                            <td colSpan={4} className="pb-3">
                              <FieldEditor sessionId={sessionId} recordId={recordId} field={field} current={working} onDone={() => setEditing(null)} />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-ink-3">
                * champ obligatoire. L'origine n'est jamais modifiée : les corrections s'appliquent à la copie de travail.
              </p>
            </section>

            <section aria-label="Exclusion" className="grid gap-2">
              <h3 className="text-sm font-semibold">Exclusion</h3>
              <ExclusionBox sessionId={sessionId} detail={data} />
            </section>

            <section aria-label="Corrections" className="grid gap-2">
              <h3 className="text-sm font-semibold">Corrections</h3>
              <CorrectionList sessionId={sessionId} corrections={data.corrections} labels={labels} />
            </section>

            <section aria-label="Constats liés" className="grid gap-2">
              <h3 className="text-sm font-semibold">Constats liés</h3>
              {data.findings.length === 0 ? (
                <p className="text-[13px] text-ink-3">Aucun constat de la dernière analyse ne porte sur cet enregistrement.</p>
              ) : (
                <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
                  {data.findings.map((f) => (
                    <li key={f.finding_id}>
                      <Link
                        to={`/sessions/${sessionId}/findings?f=${encodeURIComponent(f.finding_id)}`}
                        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-subtle"
                      >
                        <SeverityBadge severity={f.severity} short />
                        <span className="min-w-0 flex-1 truncate" title={f.explanation}>
                          {KIND_LABELS[f.kind] ?? f.kind} — {f.explanation}
                        </span>
                        <span className="text-xs whitespace-nowrap text-ink-3">{STATUS_LABELS[f.status]}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : null}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- page

export function RecordsPage() {
  const { sessionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const withExcluded = params.get("excluded") === "1";
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const selected = params.get("r");
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);
  const searchId = useId();

  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  const filters = { q: q || undefined, include_excluded: withExcluded, page, page_size: PAGE_SIZE };
  const records = useQuery({
    queryKey: ["records", sessionId, filters],
    queryFn: () => api.records(sessionId, filters),
    placeholderData: keepPreviousData,
  });

  function update(next: Record<string, string | undefined>) {
    const copy = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined) copy.delete(key);
      else copy.set(key, value);
    }
    if (!("page" in next) && !("r" in next)) copy.delete("page");
    setParams(copy, { replace: true });
  }

  const schema = session.data?.schema ?? [];
  const data = records.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Corrections</h1>
        <p className="mt-1.5 text-ink-2">
          Les fichiers importés restent intacts. Chaque correction ou exclusion s'applique à une copie de travail, avec un
          motif, et reste annulable.
        </p>
      </div>
      <StaleAnalysisBanner session={session.data} />

      <div className={cn("grid items-start gap-5", selected ? "grid-cols-[minmax(0,1fr)] xl:grid-cols-[minmax(0,1fr)_minmax(440px,540px)]" : "grid-cols-[minmax(0,1fr)]")}>
        <Card>
          <div className="flex flex-wrap items-center gap-3 px-5.5 py-3.5">
            <form
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                update({ q: text.trim() || undefined });
              }}
              className="flex h-[30px] items-center gap-1.5 rounded-md border border-line bg-surface px-2"
            >
              <Search aria-hidden className="size-3.5 text-ink-3" />
              <label htmlFor={searchId} className="sr-only">
                Rechercher un enregistrement
              </label>
              <input
                id={searchId}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Identifiant, nom, valeur…"
                className="w-60 bg-transparent text-[13px] outline-none"
              />
              {text ? (
                <button type="button" aria-label="Effacer la recherche" onClick={() => update({ q: undefined })} className="text-ink-3 hover:text-ink">
                  <X aria-hidden className="size-3.5" />
                </button>
              ) : null}
            </form>
            <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink-2">
              <input
                type="checkbox"
                checked={withExcluded}
                onChange={(event) => update({ excluded: event.target.checked ? "1" : undefined })}
                className="accent-[var(--primary)]"
              />
              Afficher les enregistrements exclus
            </label>
            {data ? <span className="ml-auto text-[13px] text-ink-3">{formatNumber(data.total)} enregistrements</span> : null}
          </div>

          {records.isError ? (
            <div className="px-5.5 pb-5">
              <ErrorState error={records.error} onRetry={() => void records.refetch()} />
            </div>
          ) : null}
          {!data || !session.data ? (
            <div className="grid gap-2 px-5.5 pb-5" aria-busy="true" aria-label="Chargement des enregistrements">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : data.total === 0 ? (
            <EmptyState icon={Search} title="Aucun enregistrement trouvé">
              Modifiez la recherche ou affichez les enregistrements exclus.
            </EmptyState>
          ) : (
            <>
              <div className={cn("overflow-x-auto transition-opacity", records.isFetching ? "opacity-60" : "")}>
                <table className="w-full border-collapse text-[13px]">
                  <thead>
                    <tr className="text-left text-xs text-ink-3">
                      <th scope="col" className="sticky left-0 h-10 border-y border-line-subtle bg-[#fcfcfd] px-4 pl-5.5 font-medium">
                        Enregistrement
                      </th>
                      {schema.map((f) => (
                        <th key={f.name} scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] px-3 font-medium whitespace-nowrap">
                          {f.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((row) => (
                      <tr
                        key={row.record_id}
                        aria-selected={selected === row.record_id}
                        onClick={() => update({ r: row.record_id })}
                        className={cn(
                          "cursor-pointer border-b border-line-subtle transition-colors hover:bg-[#f8fafc]",
                          selected === row.record_id ? "bg-brand-50" : "",
                          row.excluded ? "text-ink-3" : "",
                        )}
                      >
                        <th scope="row" className={cn("sticky left-0 bg-inherit px-4 py-2 pl-5.5 text-left font-normal whitespace-nowrap", selected === row.record_id ? "shadow-[inset_2px_0_0_var(--primary)]" : "")}>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              update({ r: row.record_id });
                            }}
                            className={cn("cursor-pointer font-mono text-xs hover:text-primary", row.excluded ? "line-through" : "")}
                          >
                            {row.record_id}
                          </button>
                          {row.excluded ? <span className="ml-2 rounded-full bg-subtle px-2 py-px text-[11px]">exclu</span> : null}
                          {row.corrected_fields.length > 0 ? (
                            <span className="ml-2 rounded-full bg-brand-100 px-2 py-px text-[11px] text-primary">
                              {row.corrected_fields.length} corrigé{row.corrected_fields.length > 1 ? "s" : ""}
                            </span>
                          ) : null}
                        </th>
                        {schema.map((f) => (
                          <td
                            key={f.name}
                            title={row.corrected_fields.includes(f.name) ? "Valeur corrigée" : undefined}
                            className={cn("max-w-64 truncate px-3 py-2 whitespace-nowrap", row.corrected_fields.includes(f.name) ? "font-medium text-primary" : "")}
                          >
                            <CellValue value={row[f.name]} />
                          </td>
                        ))}
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

        {selected && session.data ? (
          <RecordPanel sessionId={sessionId} recordId={selected} schema={schema} onClose={() => update({ r: undefined })} />
        ) : null}
      </div>
    </>
  );
}
