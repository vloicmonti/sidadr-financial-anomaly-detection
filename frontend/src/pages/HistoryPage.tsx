import { useQuery } from "@tanstack/react-query";
import { History as HistoryIcon, Search, ShieldAlert, ShieldCheck } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api } from "../api/client";
import type { AuditAction, AuditEntry, FindingStatus } from "../api/types";
import { CellValue, UndoButton } from "../components/session";
import { Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { STATUS_LABELS } from "../findings";
import { cn, formatDateTime, formatNumber } from "../lib";

const STEP = 100;
const EXCLUDED = "__excluded__";

const ACTIONS: Record<AuditAction, { label: string; tone: string }> = {
  correct: { label: "Correction", tone: "bg-brand-100 text-primary" },
  exclude: { label: "Exclusion", tone: "bg-subtle text-ink-2" },
  decide: { label: "Décision", tone: "bg-success-bg text-success" },
  undo: { label: "Annulation", tone: "bg-warning-bg text-warning" },
};

function statusLabel(value: string | null) {
  return value && value in STATUS_LABELS ? STATUS_LABELS[value as FindingStatus] : value;
}

/** Cibles encore annulables : non annulées, et pour une décision, non remplacée par une plus récente. */
function undoableTargets(entries: AuditEntry[]): Set<string> {
  const undone = new Set(entries.filter((e) => e.action_type === "undo" && e.target).map((e) => e.target));
  const latestDecision = new Map<string, string>();
  for (const e of entries) if (e.action_type === "decide" && e.finding_id && e.target) latestDecision.set(e.finding_id, e.target);
  const result = new Set<string>();
  for (const e of entries) {
    if (!e.target || undone.has(e.target) || e.action_type === "undo") continue;
    if (e.action_type === "decide" && latestDecision.get(e.finding_id ?? "") !== e.target) continue;
    result.add(e.target);
  }
  return result;
}

function Change({ entry, sessionId, labels }: { entry: AuditEntry; sessionId: string; labels: Map<string, string> }) {
  const field = entry.field ? labels.get(entry.field) ?? entry.field : null;
  switch (entry.action_type) {
    case "correct":
      return (
        <>
          <b className="font-medium">{field}</b> : <CellValue value={entry.old_value} /> → <CellValue value={entry.new_value} />
        </>
      );
    case "exclude":
      return <>Écarté de la copie de travail</>;
    case "decide":
      return (
        <>
          {entry.old_value ? `${statusLabel(entry.old_value)} → ` : ""}
          <b className="font-medium">{statusLabel(entry.new_value)}</b>
        </>
      );
    case "undo":
      return (
        <>
          Annule <span className="font-mono text-xs">{entry.target}</span>
          {entry.field && entry.field !== EXCLUDED ? (
            <>
              {" "}
              · {field} rétabli à <CellValue value={entry.new_value} />
            </>
          ) : entry.finding_id ? (
            <>
              {" "}
              · décision sur{" "}
              <Link to={`/sessions/${sessionId}/findings?f=${encodeURIComponent(entry.finding_id)}`} className="text-primary hover:underline">
                {entry.finding_id}
              </Link>
            </>
          ) : null}
        </>
      );
  }
}

export function HistoryPage() {
  const { sessionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const action = (params.get("action") as AuditAction | null) ?? undefined;
  const [text, setText] = useState("");
  const [shown, setShown] = useState(STEP);
  const searchId = useId();
  const history = useQuery({ queryKey: ["history", sessionId], queryFn: () => api.history(sessionId) });
  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  const labels = useMemo(() => new Map((session.data?.schema ?? []).map((f) => [f.name, f.label])), [session.data]);

  const entries = history.data?.entries;
  const undoable = useMemo(() => undoableTargets(entries ?? []), [entries]);
  const visible = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return [...(entries ?? [])]
      .reverse()
      .filter((e) => !action || e.action_type === action)
      .filter(
        (e) =>
          !needle ||
          [e.target, e.record_id, e.finding_id, e.field, e.reason, e.actor, e.old_value, e.new_value].some((v) => v?.toLowerCase().includes(needle)),
      );
  }, [entries, action, text]);

  if (history.isError) return <ErrorState error={history.error} onRetry={() => void history.refetch()} />;
  const integrity = history.data?.integrity;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Historique</h1>
        <p className="mt-1.5 text-ink-2">
          Journal d'audit de la session : chaque correction, exclusion, décision et annulation, avec sa date, son auteur et
          son motif. Le journal ne s'efface pas : une annulation est elle-même une nouvelle entrée.
        </p>
      </div>

      {integrity ? (
        integrity.valid ? (
          <div role="status" className="mb-4 flex items-center gap-2 rounded-lg bg-success-bg px-4 py-2.5 text-[13px] text-success">
            <ShieldCheck aria-hidden className="size-4 shrink-0" />
            Journal intègre : {formatNumber(integrity.entries)} entrée(s), chaînage des empreintes vérifié.
          </div>
        ) : (
          <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-danger-line bg-danger-bg px-4 py-2.5 text-[13px] text-danger">
            <ShieldAlert aria-hidden className="size-4 shrink-0" />
            Journal altéré à partir de l'entrée #{integrity.first_broken_entry} : le fichier a été modifié en dehors de SIDADR.
          </div>
        )
      ) : null}

      <Card>
        <div className="flex flex-wrap items-center gap-3 px-5.5 py-3.5">
          <select
            aria-label="Filtrer par action"
            value={action ?? ""}
            onChange={(event) => {
              const copy = new URLSearchParams(params);
              if (event.target.value) copy.set("action", event.target.value);
              else copy.delete("action");
              setParams(copy, { replace: true });
            }}
            className="h-[30px] rounded-md border border-line bg-surface px-2 text-[13px]"
          >
            <option value="">Toutes les actions</option>
            {(Object.keys(ACTIONS) as AuditAction[]).map((a) => (
              <option key={a} value={a}>
                {ACTIONS[a].label}
              </option>
            ))}
          </select>
          <div className="flex h-[30px] items-center gap-1.5 rounded-md border border-line bg-surface px-2">
            <Search aria-hidden className="size-3.5 text-ink-3" />
            <label htmlFor={searchId} className="sr-only">
              Filtrer le journal
            </label>
            <input
              id={searchId}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Enregistrement, constat, motif, auteur…"
              className="w-64 bg-transparent text-[13px] outline-none"
            />
          </div>
          {entries ? <span className="ml-auto text-[13px] text-ink-3">{formatNumber(visible.length)} entrée(s)</span> : null}
        </div>

        {history.isPending ? (
          <div className="grid gap-2 px-5.5 pb-5" aria-busy="true" aria-label="Chargement du journal">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <EmptyState icon={HistoryIcon} title={entries?.length ? "Aucune entrée pour ces filtres" : "Journal vide"}>
            {entries?.length
              ? "Modifiez ou retirez les filtres."
              : "Les décisions prises sur les constats et les corrections apportées aux données apparaîtront ici."}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-xs text-ink-3">
                  {["#", "Date", "Auteur", "Action", "Objet", "Changement", "Motif"].map((h, i) => (
                    <th key={h} scope="col" className={cn("h-10 border-y border-line-subtle bg-[#fcfcfd] px-3 font-medium whitespace-nowrap", i === 0 ? "pl-5.5" : "")}>
                      {h}
                    </th>
                  ))}
                  <th scope="col" className="h-10 border-y border-line-subtle bg-[#fcfcfd] pr-5.5" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {visible.slice(0, shown).map((e) => (
                  <tr key={e.entry_id} className="border-b border-line-subtle align-top">
                    <td className="px-3 py-2.5 pl-5.5 font-mono text-xs text-ink-3">{e.entry_id}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-ink-2">{formatDateTime(e.ts_utc)}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap">{e.actor}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <span className={cn("rounded-full px-2 py-px text-xs font-medium", ACTIONS[e.action_type].tone)}>{ACTIONS[e.action_type].label}</span>
                      {e.target ? <span className="ml-1.5 font-mono text-xs text-ink-3">{e.target}</span> : null}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {e.record_id ? (
                        <Link to={`/sessions/${sessionId}/records?r=${encodeURIComponent(e.record_id)}`} className="font-mono text-xs text-primary hover:underline">
                          {e.record_id}
                        </Link>
                      ) : e.finding_id ? (
                        <Link to={`/sessions/${sessionId}/findings?f=${encodeURIComponent(e.finding_id)}`} className="font-mono text-xs text-primary hover:underline">
                          {e.finding_id}
                        </Link>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                    <td className="max-w-72 min-w-48 px-3 py-2.5 break-words">
                      <Change entry={e} sessionId={sessionId} labels={labels} />
                    </td>
                    <td className="max-w-80 min-w-48 px-3 py-2.5 break-words text-ink-2">{e.reason}</td>
                    <td className="px-3 py-1.5 pr-5.5 text-right">
                      {e.target && undoable.has(e.target) ? <UndoButton sessionId={sessionId} target={e.target} /> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {visible.length > shown ? (
              <div className="flex justify-center py-3">
                <Button className="h-8" onClick={() => setShown((n) => n + STEP)}>
                  Afficher {Math.min(STEP, visible.length - shown)} entrées de plus
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </Card>
    </>
  );
}
