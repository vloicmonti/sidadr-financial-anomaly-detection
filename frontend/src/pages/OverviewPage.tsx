import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, FileText, LoaderCircle, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { useParams } from "react-router";

import { api } from "../api/client";
import type { AnalysisSummary, SessionState, Severity } from "../api/types";
import { Button, Card, CardHeader, EmptyState, ErrorState, SEVERITIES, SeverityBadge, Skeleton } from "../components/ui";
import { cn, formatDateTime, formatNumber, formatPercent } from "../lib";

const SEVERITY_HELP: Record<"banking" | "generic", Record<Severity, string>> = {
  banking: {
    certain_error: "Formats invalides, dates impossibles, identifiants en double",
    probable_inconsistency: "Règles métier non respectées, doublons inter-fichiers",
    atypical_value: "Écarts statistiques à vérifier — pas des erreurs",
    review_alert: "Correspondances de noms et clients multi-banques à valider",
  },
  generic: {
    certain_error: "Champs obligatoires vides, lignes ou identifiants en double",
    probable_inconsistency: "Règles personnalisées non respectées",
    atypical_value: "Valeurs numériques inhabituelles — pas des erreurs",
    review_alert: "Constats demandant un jugement humain",
  },
};

function ScoreRing({ score }: { score: number }) {
  const circumference = 2 * Math.PI * 42;
  return (
    <div className="relative size-37" role="img" aria-label={`Score qualité ${score.toFixed(1)} sur 100`}>
      <svg viewBox="0 0 100 100" className="size-37 -rotate-90">
        <circle cx="50" cy="50" r="42" fill="none" strokeWidth="8" className="stroke-line-subtle" />
        <circle
          cx="50"
          cy="50"
          r="42"
          fill="none"
          strokeWidth="8"
          strokeLinecap="round"
          className="stroke-primary"
          strokeDasharray={`${(score / 100) * circumference} ${circumference}`}
        />
      </svg>
      <div className="absolute inset-0 grid place-content-center text-center">
        <b className="text-[30px] leading-none font-semibold tracking-tight tabular-nums">
          {score.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
        </b>
        <span className="mt-1 text-xs text-ink-3">sur 100</span>
      </div>
    </div>
  );
}

function ScoreCard({ analysis }: { analysis: AnalysisSummary }) {
  const { score } = analysis;
  return (
    <Card>
      <CardHeader title="Score qualité" hint={`sur ${formatNumber(analysis.rows_analyzed)} enregistrements`} />
      <div className="grid grid-cols-[148px_1fr] items-center gap-7 px-5.5 pt-4.5 pb-5.5">
        <ScoreRing score={score.score} />
        <div className="grid gap-3.5">
          {score.dimensions.map((d) => (
            <div key={d.name}>
              <div className="mb-1.5 flex justify-between text-[13px]">
                <span className="text-ink-2" title={d.definition}>
                  {d.label}
                </span>
                {d.value === null ? (
                  <span className="text-ink-3">non évaluée</span>
                ) : (
                  <b className="font-semibold tabular-nums">
                    {formatPercent(d.value)}
                    <small className="ml-1.5 font-normal text-ink-3">
                      {formatNumber(d.numerator)} / {formatNumber(d.denominator)}
                    </small>
                  </b>
                )}
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-line-subtle">
                <span className="block h-full rounded-full bg-brand-600" style={{ width: `${100 * (d.value ?? 0)}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
      <p className="mx-5.5 mb-5.5 rounded-md bg-app px-3 py-2.5 text-xs text-ink-2">{score.notes.join(" ")}</p>
    </Card>
  );
}

function FindingsCard({ analysis, mode }: { analysis: AnalysisSummary; mode: "banking" | "generic" }) {
  const bySeverity = analysis.findings.by_severity;
  return (
    <Card>
      <CardHeader title="À traiter" hint={`${formatNumber(analysis.findings.total)} constats`} />
      <div className="grid grid-cols-2 gap-3 px-5.5 pt-4.5 pb-5.5">
        {(Object.keys(SEVERITIES) as Severity[]).map((severity) => (
          <div key={severity} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 rounded-lg border border-line px-4 py-3.5">
            <span className="text-[28px] leading-none font-semibold tracking-tight tabular-nums">
              {formatNumber(bySeverity[severity] ?? 0)}
            </span>
            <SeverityBadge severity={severity} />
            <span className="col-span-2 text-xs leading-snug text-ink-3">{SEVERITY_HELP[mode][severity]}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function SessionFacts({ session }: { session: SessionState }) {
  const facts = [
    ["Corrections actives", session.active_corrections],
    ["Enregistrements exclus", session.excluded_records],
    ["Décisions", session.active_decisions],
    ["Entrées du journal", session.journal_entries],
  ] as const;
  const intact = session.journal_integrity === "intact";
  return (
    <div className="mt-5 grid grid-cols-[1fr_1fr] gap-5">
      <Card>
        <CardHeader title="Revue" />
        <dl className="grid grid-cols-2 gap-3 px-5.5 pt-4 pb-5.5">
          {facts.map(([label, value]) => (
            <div key={label} className="rounded-md bg-app px-3.5 py-3">
              <dt className="text-xs text-ink-3">{label}</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums">{formatNumber(value)}</dd>
            </div>
          ))}
        </dl>
        <p className={cn("mx-5.5 mb-5.5 flex items-center gap-2 text-[13px]", intact ? "text-success" : "text-danger")}>
          {intact ? <ShieldCheck aria-hidden className="size-4" /> : <CircleAlert aria-hidden className="size-4" />}
          {intact ? "Journal d'audit intact (chaîne d'empreintes vérifiée)" : `Journal ${session.journal_integrity}`}
        </p>
      </Card>
      <Card>
        <CardHeader title="Fichiers de la session" />
        <ul className="px-5.5 pt-2.5 pb-4">
          {session.imports.map((file) => (
            <li key={file.import_id} className="flex items-center gap-3 border-b border-line-subtle py-2.5 text-[13px] last:border-b-0">
              <span className="grid size-[34px] shrink-0 place-items-center rounded-lg bg-brand-50 text-primary">
                <FileText aria-hidden className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{file.source_file}</span>
                <span className="block text-xs text-ink-3">
                  {formatNumber(file.rows)} lignes importées
                  {file.rows_rejected ? ` · ${file.rows_rejected} rejetées` : ""}
                </span>
              </span>
              <span className="font-mono text-xs text-ink-3">{file.import_id}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function OverviewPage() {
  const { sessionId = "" } = useParams();
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  const analyze = useMutation({
    mutationFn: () => api.analyze(sessionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["session", sessionId] }),
  });

  if (session.isPending) {
    return (
      <div className="grid gap-5" aria-busy="true" aria-label="Chargement de la session">
        <Skeleton className="h-14 w-1/2" />
        <div className="grid grid-cols-2 gap-5">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }
  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;

  const data = session.data;
  const running = analyze.isPending;
  const analyzeButton = (
    <Button
      variant="primary"
      icon={running ? undefined : data.analysis ? RefreshCw : Play}
      disabled={running}
      aria-busy={running}
      onClick={() => analyze.mutate()}
    >
      {running ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {running ? "Analyse en cours…" : data.analysis ? "Relancer l'analyse" : "Lancer l'analyse"}
    </Button>
  );

  return (
    <>
      <div className="mb-6 flex items-end gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">Vue d'ensemble</h1>
          <p className="mt-1.5 text-ink-2">
            {data.name} · créée le {formatDateTime(data.created_at)}
            {data.analysis ? ` · analysée le ${formatDateTime(data.analysis.generated_at)}` : ""}
          </p>
        </div>
        <div className="ml-auto">{analyzeButton}</div>
      </div>

      {analyze.isError ? (
        <div className="mb-5">
          <ErrorState error={analyze.error} onRetry={() => analyze.mutate()} />
        </div>
      ) : null}

      {data.analysis && !data.analysis_up_to_date ? (
        <div role="status" className="mb-5 flex items-center gap-3 rounded-lg bg-warning-bg px-4 py-3 text-[13px] text-warning">
          <CircleAlert aria-hidden className="size-4 shrink-0" />
          Des corrections ou décisions ont été enregistrées depuis la dernière analyse : relancez-la pour un état à jour.
        </div>
      ) : null}

      {data.analysis ? (
        <div className="grid grid-cols-[minmax(380px,1fr)_minmax(0,1.25fr)] gap-5">
          <ScoreCard analysis={data.analysis} />
          <FindingsCard analysis={data.analysis} mode={data.mode ?? "banking"} />
        </div>
      ) : (
        <Card>
          <EmptyState icon={Play} title="Cette session n'a pas encore été analysée" action={analyzeButton}>
            L'analyse lance tous les détecteurs sur les {formatNumber(data.working_rows)} enregistrements de travail :
            profil, doublons, noms similaires, clients multi-banques, valeurs atypiques et règles métier. Comptez
            de 15 à 40 secondes pour 20 000 lignes.
          </EmptyState>
        </Card>
      )}

      <SessionFacts session={data} />
    </>
  );
}
