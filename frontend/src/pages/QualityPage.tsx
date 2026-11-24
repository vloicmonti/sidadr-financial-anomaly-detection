import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, CircleAlert } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import type { AnalysisSummary, ColumnProfile } from "../api/types";
import { StaleAnalysisBanner } from "../components/session";
import { Card, CardHeader, EmptyState, ErrorState, SeverityBadge, Skeleton } from "../components/ui";
import { cn, formatDateTime, formatNumber, formatPercent } from "../lib";

const TYPE_LABELS: Record<string, string> = {
  text: "texte",
  decimal: "nombre",
  date: "date",
  enum: "liste de valeurs",
};

/** Les statistiques arrivent en texte décimal exact ; on les affiche à la française. */
function decimal(value: string | number): string {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toLocaleString("fr-FR", { maximumFractionDigits: 3 }) : String(value);
}

const TH = "h-10 border-y border-line-subtle bg-[#fcfcfd] px-3 text-left text-xs font-medium whitespace-nowrap text-ink-3";

function Meter({ value, tone = "bg-brand-600" }: { value: number; tone?: string }) {
  return (
    <span aria-hidden className="block h-1.5 w-24 overflow-hidden rounded-full bg-line-subtle">
      <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.round(100 * value)}%` }} />
    </span>
  );
}

// --------------------------------------------------------------------------- score

function Dimensions({ analysis }: { analysis: AnalysisSummary }) {
  const { score } = analysis;
  // La note d'une dimension n'est utile que si elle ne répète ni sa définition ni les notes générales.
  const adds = (note: string, definition: string) =>
    Boolean(note) && !definition.includes(note.slice(-30)) && !score.notes.some((n) => n.includes(note));
  return (
    <Card>
      <CardHeader
        title="Score qualité"
        hint={`${score.score.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} / 100 · moyenne pondérée des dimensions évaluées`}
      />
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th scope="col" className={cn(TH, "pl-5.5")}>Dimension</th>
              <th scope="col" className={TH}>Mesure</th>
              <th scope="col" className={cn(TH, "text-right")}>Valeur</th>
              <th scope="col" className={cn(TH, "text-right")}>En défaut</th>
              <th scope="col" className={cn(TH, "pr-5.5 text-right")}>Poids effectif</th>
            </tr>
          </thead>
          <tbody>
            {score.dimensions.map((d) => (
              <tr key={d.name} className="border-b border-line-subtle align-top">
                <th scope="row" className="px-3 py-2.5 pl-5.5 text-left font-medium whitespace-nowrap">
                  {d.label}
                </th>
                <td className="px-3 py-2.5 text-ink-2">
                  {d.definition}
                  {adds(d.note, d.definition) ? <span className="mt-0.5 block text-xs text-ink-3">{d.note}</span> : null}
                </td>
                <td className="px-3 py-2.5 text-right font-semibold whitespace-nowrap tabular-nums">
                  {d.value === null ? <span className="font-normal text-ink-3">non évaluée</span> : formatPercent(d.value)}
                </td>
                <td className="px-3 py-2.5 text-right whitespace-nowrap tabular-nums text-ink-2">
                  {d.value === null ? "—" : `${formatNumber(d.numerator)} sur ${formatNumber(d.denominator)}`}
                </td>
                <td className="px-3 py-2.5 pr-5.5 text-right whitespace-nowrap tabular-nums text-ink-2">{formatPercent(d.effective_weight)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {score.notes.length ? <p className="mx-5.5 my-4 rounded-md bg-app px-3 py-2.5 text-xs text-ink-2">{score.notes.join(" ")}</p> : <div className="h-4" />}
    </Card>
  );
}

// --------------------------------------------------------------------------- règles

function Rules({ analysis, sessionId }: { analysis: AnalysisSummary; sessionId: string }) {
  return (
    <Card>
      <CardHeader
        title="Règles métier"
        hint={`${analysis.rules.length} règle(s) évaluée(s)`}
        action={
          <Link to={`/sessions/${sessionId}/findings/rules`} className="text-[13px] font-medium text-primary hover:underline">
            Voir les constats
          </Link>
        }
      />
      {analysis.rules.length === 0 ? (
        <p className="px-5.5 py-5 text-[13px] text-ink-3">Aucune règle métier n'a été évaluée pour cette session.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th scope="col" className={cn(TH, "pl-5.5")}>Règle</th>
                <th scope="col" className={TH}>Sévérité</th>
                <th scope="col" className={cn(TH, "text-right")}>Vérifiées</th>
                <th scope="col" className={cn(TH, "text-right")}>Violations</th>
                <th scope="col" className={cn(TH, "pr-5.5 text-right")}>Non évaluables</th>
              </tr>
            </thead>
            <tbody>
              {analysis.rules.map((r) => (
                <tr key={r.rule_id} className="border-b border-line-subtle">
                  <th scope="row" className="px-3 py-2.5 pl-5.5 text-left font-normal">
                    {r.description}
                    <span className="block font-mono text-xs text-ink-3">{r.rule_id}</span>
                  </th>
                  <td className="px-3 py-2.5">
                    <SeverityBadge severity={r.severity} />
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{formatNumber(r.checked)}</td>
                  <td className={cn("px-3 py-2.5 text-right tabular-nums", r.violations ? "font-semibold text-danger" : "text-ink-3")}>
                    {formatNumber(r.violations)}
                  </td>
                  <td className={cn("px-3 py-2.5 pr-5.5 text-right tabular-nums", r.not_evaluable ? "text-warning" : "text-ink-3")}>
                    {formatNumber(r.not_evaluable)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="h-4" />
    </Card>
  );
}

// --------------------------------------------------------------------------- profil

function ColumnDetail({ column, sessionId }: { column: ColumnProfile; sessionId: string }) {
  const reasons = Object.entries(column.invalid_reasons);
  return (
    <div className="grid gap-4 px-5.5 py-4 text-[13px] sm:grid-cols-2">
      {column.numeric ? (
        <div>
          <h4 className="mb-1.5 font-medium">Distribution</h4>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 tabular-nums">
            {(
              [
                ["Minimum", decimal(column.numeric.min)],
                ["1er quartile", decimal(column.numeric.q1)],
                ["Médiane", decimal(column.numeric.median)],
                ["3e quartile", decimal(column.numeric.q3)],
                ["Maximum", decimal(column.numeric.max)],
                ["Moyenne", decimal(column.numeric.mean)],
                ["Écart-type", decimal(column.numeric.std)],
              ] as const
            ).map(([label, value]) => (
              <Fragment key={label}>
                <dt className="text-ink-3">{label}</dt>
                <dd>{value}</dd>
              </Fragment>
            ))}
          </dl>
        </div>
      ) : null}
      {column.date_range ? (
        <div>
          <h4 className="mb-1.5 font-medium">Période couverte</h4>
          <p>
            du {column.date_range[0]} au {column.date_range[1]}
          </p>
        </div>
      ) : null}
      {column.top_values.length ? (
        <div>
          <h4 className="mb-1.5 font-medium">Valeurs les plus fréquentes</h4>
          <ol className="grid gap-0.5">
            {column.top_values.map(([value, count]) => (
              <li key={value} className="flex gap-3">
                <span className="min-w-0 flex-1 truncate" title={value}>
                  {value}
                </span>
                <span className="text-ink-3 tabular-nums">{formatNumber(count)}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {reasons.length ? (
        <div>
          <h4 className="mb-1.5 font-medium">Valeurs non conformes</h4>
          <ul className="grid gap-0.5">
            {reasons.map(([reason, count]) => (
              <li key={reason} className="flex gap-3">
                <span className="flex-1">{reason}</span>
                <span className="text-danger tabular-nums">{formatNumber(count)}</span>
              </li>
            ))}
          </ul>
          {column.invalid_samples.length ? (
            <p className="mt-2 text-xs text-ink-3">
              Exemples :{" "}
              {column.invalid_samples.slice(0, 8).map((id, i) => (
                <Fragment key={id}>
                  {i ? ", " : null}
                  <Link to={`/sessions/${sessionId}/records?r=${encodeURIComponent(id)}`} className="font-mono text-primary hover:underline">
                    {id}
                  </Link>
                </Fragment>
              ))}
              {column.invalid > 8 ? "…" : null}
            </p>
          ) : null}
        </div>
      ) : null}
      {column.decisions.length ? (
        <div className="sm:col-span-2">
          <h4 className="mb-1.5 font-medium">Choix d'interprétation du moteur</h4>
          <ul className="list-disc pl-5 text-ink-2">
            {column.decisions.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Profile({ analysis, sessionId }: { analysis: AnalysisSummary; sessionId: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const profile = analysis.profile;
  if (!profile) {
    return (
      <Card>
        <EmptyState icon={CircleAlert} title="Profil par colonne indisponible">
          Cette analyse a été produite par une version antérieure. Relancez-la depuis la vue d'ensemble pour obtenir le profil
          de chaque colonne.
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader
        title="Profil des colonnes"
        hint={`${profile.columns.length} colonnes · ${formatNumber(profile.rows)} enregistrements · ${formatNumber(profile.duplicate_rows)} ligne(s) en doublon complet`}
      />
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th scope="col" className={cn(TH, "pl-5.5")}>Colonne</th>
              <th scope="col" className={TH}>Type attendu</th>
              <th scope="col" className={TH}>Remplissage</th>
              <th scope="col" className={cn(TH, "text-right")}>Distinctes</th>
              <th scope="col" className={cn(TH, "pr-5.5")}>Conformité</th>
            </tr>
          </thead>
          <tbody>
            {profile.columns.map((c) => {
              const filled = c.rows ? (c.rows - c.missing) / c.rows : 1;
              const conformity = c.rows - c.missing ? 1 - c.invalid / (c.rows - c.missing) : 1;
              const expanded = open === c.name;
              return (
                <Fragment key={c.name}>
                  <tr className={cn("border-b border-line-subtle", expanded ? "bg-brand-50/50" : "hover:bg-[#f8fafc]")}>
                    <th scope="row" className="px-3 py-2.5 pl-4 text-left font-medium">
                      <button
                        type="button"
                        aria-expanded={expanded}
                        aria-label={`Détail de ${c.label}`}
                        onClick={() => setOpen(expanded ? null : c.name)}
                        className="flex cursor-pointer items-center gap-1.5 text-left hover:text-primary"
                      >
                        {expanded ? <ChevronDown aria-hidden className="size-4 text-ink-3" /> : <ChevronRight aria-hidden className="size-4 text-ink-3" />}
                        {c.label}
                        {c.required ? <span className="text-ink-3">*</span> : null}
                      </button>
                    </th>
                    <td className="px-3 py-2.5 whitespace-nowrap text-ink-2">{TYPE_LABELS[c.type] ?? c.type}</td>
                    <td className="px-3 py-2.5">
                      <span className="flex items-center gap-2.5 whitespace-nowrap tabular-nums">
                        <Meter value={filled} tone={c.required && c.missing ? "bg-warning" : "bg-brand-600"} />
                        {formatPercent(filled)}
                        {c.missing ? <span className="text-xs text-ink-3">{formatNumber(c.missing)} vide(s)</span> : null}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatNumber(c.distinct)}</td>
                    <td className="px-3 py-2.5 pr-5.5 whitespace-nowrap tabular-nums">
                      {c.checked_for_validity ? (
                        <span className="flex items-center gap-2.5">
                          <Meter value={conformity} tone={c.invalid ? "bg-danger" : "bg-success"} />
                          {formatPercent(conformity)}
                          {c.invalid ? <span className="text-xs text-danger">{formatNumber(c.invalid)} invalide(s)</span> : null}
                        </span>
                      ) : (
                        <span className="text-xs text-ink-3" title="Le texte libre n'a pas de format à vérifier.">
                          non contrôlée
                        </span>
                      )}
                    </td>
                  </tr>
                  {expanded ? (
                    <tr className="border-b border-line-subtle bg-brand-50/30">
                      <td colSpan={5}>
                        <ColumnDetail column={c} sessionId={sessionId} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mx-5.5 my-4 text-xs text-ink-3">
        * champ obligatoire. La conformité n'est contrôlée que pour les nombres, les dates et les listes de valeurs.
      </p>
    </Card>
  );
}

// --------------------------------------------------------------------------- page

export function QualityPage() {
  const { sessionId = "" } = useParams();
  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });

  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;
  const analysis = session.data?.analysis;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Qualité des données</h1>
        <p className="mt-1.5 text-ink-2">
          Détail du score, résultat de chaque règle et profil de chaque colonne, tels que mesurés par la dernière analyse
          {analysis ? ` (${formatDateTime(analysis.generated_at)})` : ""}.
        </p>
      </div>
      <StaleAnalysisBanner session={session.data} />
      {session.isPending ? (
        <div className="grid gap-4" aria-busy="true" aria-label="Chargement">
          <Skeleton className="h-48" />
          <Skeleton className="h-72" />
        </div>
      ) : !analysis ? (
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
            Lancez l'analyse depuis la vue d'ensemble pour mesurer la qualité des données.
          </EmptyState>
        </Card>
      ) : (
        <div className="grid gap-5">
          <Dimensions analysis={analysis} />
          <Profile analysis={analysis} sessionId={sessionId} />
          <Rules analysis={analysis} sessionId={sessionId} />
        </div>
      )}
    </>
  );
}
