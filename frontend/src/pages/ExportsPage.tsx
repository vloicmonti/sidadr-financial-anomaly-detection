import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Download, ExternalLink, FileSpreadsheet, FileText, FolderArchive, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import type { SessionState } from "../api/types";
import { StaleAnalysisBanner } from "../components/session";
import { Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { cn, formatDateTime, formatNumber } from "../lib";

const LINK =
  "inline-flex h-9 items-center gap-2 rounded-md px-3.5 font-medium whitespace-nowrap transition-colors";
const PRIMARY = cn(LINK, "bg-primary text-white hover:bg-primary-hover");
const SECONDARY = cn(LINK, "border border-line bg-surface text-ink shadow-sm hover:bg-subtle");

function Output({
  icon: Icon,
  title,
  format,
  children,
  actions,
}: {
  icon: LucideIcon;
  title: string;
  format: string;
  children: ReactNode;
  actions: ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <div className="flex flex-1 gap-4 px-5.5 pt-5">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-brand-50 text-primary">
          <Icon aria-hidden className="size-5" />
        </span>
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">
            {title} <span className="ml-1 text-xs font-normal text-ink-3">{format}</span>
          </h2>
          <div className="mt-1.5 text-[13px] text-ink-2">{children}</div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap justify-end gap-2 border-t border-line-subtle px-5.5 py-3.5">{actions}</div>
    </Card>
  );
}

function Traceability({ session }: { session: SessionState }) {
  const rows: [string, ReactNode][] = [
    ["Fichiers sources", session.imports.map((i) => i.source_file).join(", ")],
    ["Enregistrements importés", formatNumber(session.rows)],
    ["Enregistrements de travail", `${formatNumber(session.working_rows)} (${formatNumber(session.excluded_records)} exclu(s))`],
    ["Corrections actives", formatNumber(session.active_corrections)],
    ["Décisions actives", formatNumber(session.active_decisions)],
    [
      "Journal d'audit",
      <span key="j" className={session.journal_integrity === "intact" ? "text-success" : "font-medium text-danger"}>
        {formatNumber(session.journal_entries)} entrée(s), {session.journal_integrity}
      </span>,
    ],
    ["Dernière analyse", session.analysis ? formatDateTime(session.analysis.generated_at) : "aucune"],
  ];
  return (
    <Card>
      <div className="px-5.5 pt-5">
        <h2 className="text-[15px] font-semibold tracking-tight">Ce que contiennent les sorties</h2>
        <p className="mt-1 text-[13px] text-ink-2">
          Chaque sortie reprend l'état ci-dessous et le documente : sources, empreinte des données d'origine, corrections,
          décisions et intégrité du journal.
        </p>
      </div>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 px-5.5 pb-5 text-[13px]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-ink-3">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export function ExportsPage() {
  const { sessionId = "" } = useParams();
  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;

  const base = `/api/sessions/${encodeURIComponent(sessionId)}`;
  const data = session.data;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Rapports & exports</h1>
        <p className="mt-1.5 text-ink-2">
          Les sorties sont produites à la demande à partir de la copie de travail : données corrigées, constats avec leur
          statut de revue et journal d'audit. Les fichiers d'origine ne sont jamais modifiés.
        </p>
      </div>
      <StaleAnalysisBanner session={data} />

      {session.isPending || !data ? (
        <div className="grid gap-4 xl:grid-cols-3" aria-busy="true" aria-label="Chargement">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : (
        <div className="grid gap-5">
          <div className="grid gap-5 xl:grid-cols-3">
            <Output
              icon={FileText}
              title="Rapport qualité"
              format="HTML autonome"
              actions={
                data.analysis ? (
                  <>
                    <a href={`${base}/report`} target="_blank" rel="noopener noreferrer" className={SECONDARY}>
                      <ExternalLink aria-hidden className="size-4" />
                      Ouvrir
                    </a>
                    <a href={`${base}/report`} download={`sidadr_${sessionId}_rapport.html`} className={PRIMARY}>
                      <Download aria-hidden className="size-4" />
                      Télécharger
                    </a>
                  </>
                ) : (
                  <span className="text-[13px] text-ink-3">Disponible après une analyse</span>
                )
              }
            >
              Score et dimensions, constats par sévérité avec exemples, règles métier appliquées, corrections et décisions,
              traçabilité des sources. Un seul fichier, lisible hors ligne et imprimable.
            </Output>
            <Output
              icon={FileSpreadsheet}
              title="Classeur Excel"
              format="XLSX"
              actions={
                <a href={`${base}/export?format=xlsx`} download className={PRIMARY}>
                  <Download aria-hidden className="size-4" />
                  Télécharger
                </a>
              }
            >
              Quatre feuilles : Données (copie de travail corrigée), Constats (avec leur statut de revue), Journal et
              Traçabilité. Toutes les cellules sont écrites en texte : aucune formule n'est exécutée à l'ouverture.
            </Output>
            <Output
              icon={FolderArchive}
              title="Fichiers CSV"
              format="archive ZIP"
              actions={
                <a href={`${base}/export?format=csv`} download className={PRIMARY}>
                  <Download aria-hidden className="size-4" />
                  Télécharger
                </a>
              }
            >
              Les mêmes tables en CSV (UTF-8, séparateur point-virgule) et un fichier de métadonnées JSON. Les valeurs qui
              ressembleraient à une formule de tableur sont neutralisées.
            </Output>
          </div>

          {!data.analysis ? (
            <Card>
              <EmptyState
                icon={CircleAlert}
                title="Aucune analyse pour cette session"
                action={
                  <Link to={`/sessions/${sessionId}`} className="font-medium text-primary hover:underline">
                    Aller à la vue d'ensemble
                  </Link>
                }
              >
                Les exports sont possibles, mais ils ne contiendront aucun constat tant que l'analyse n'a pas été lancée.
              </EmptyState>
            </Card>
          ) : null}
          <Traceability session={data} />
        </div>
      )}
    </>
  );
}
