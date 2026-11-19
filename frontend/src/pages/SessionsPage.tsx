import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FolderOpen } from "lucide-react";
import { Link } from "react-router";

import { api } from "../api/client";
import { Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { formatDateTime, formatNumber } from "../lib";

export function SessionsPage() {
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        <p className="mt-1.5 text-ink-2">
          Chaque session regroupe des fichiers importés, leur analyse, les décisions et le journal d'audit.
        </p>
      </div>

      {sessions.isError ? <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} /> : null}

      <Card>
        {sessions.isPending ? (
          <div className="grid gap-3 p-5.5" aria-busy="true" aria-label="Chargement des sessions">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-11" />
            ))}
          </div>
        ) : sessions.data && sessions.data.length === 0 ? (
          <EmptyState icon={FolderOpen} title="Aucune session pour l'instant">
            Créez une session en ligne de commande :{" "}
            <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs">
              sidadr session-create FICHIERS --accept-proposed
            </code>
            . L'import depuis l'interface arrive avec les prochains écrans.
          </EmptyState>
        ) : sessions.data ? (
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-xs text-ink-3">
                <th className="h-10 border-b border-line-subtle bg-[#fcfcfd] px-4 pl-5.5 font-medium">Session</th>
                <th className="h-10 border-b border-line-subtle bg-[#fcfcfd] px-4 font-medium">Fichiers</th>
                <th className="h-10 border-b border-line-subtle bg-[#fcfcfd] px-4 text-right font-medium">
                  Enregistrements
                </th>
                <th className="h-10 border-b border-line-subtle bg-[#fcfcfd] px-4 font-medium">Créée le</th>
                <th className="h-10 border-b border-line-subtle bg-[#fcfcfd] px-4 pr-5.5" aria-label="Ouvrir" />
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((s) => (
                <tr key={s.session_id} className="group transition-colors hover:bg-[#f8fafc]">
                  <td className="h-14 border-b border-line-subtle px-4 pl-5.5">
                    <Link to={`/sessions/${s.session_id}`} className="font-medium hover:text-primary">
                      {s.name}
                    </Link>
                    <div className="mt-0.5 font-mono text-xs text-ink-3">{s.session_id}</div>
                  </td>
                  <td className="border-b border-line-subtle px-4 text-[13px] text-ink-2">
                    {s.imports.map((i) => i.source_file).join(", ")}
                  </td>
                  <td className="border-b border-line-subtle px-4 text-right tabular-nums">{formatNumber(s.rows)}</td>
                  <td className="border-b border-line-subtle px-4 text-[13px] text-ink-2">{formatDateTime(s.created_at)}</td>
                  <td className="border-b border-line-subtle px-4 pr-5.5 text-right">
                    <Link
                      to={`/sessions/${s.session_id}`}
                      aria-label={`Ouvrir ${s.name}`}
                      className="inline-grid size-8 place-items-center rounded-md text-ink-3 group-hover:text-primary"
                    >
                      <ChevronRight aria-hidden className="size-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>
    </>
  );
}
