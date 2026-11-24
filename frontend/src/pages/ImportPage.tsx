import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Plus,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useId, useMemo, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/client";
import type {
  FileInspection,
  InferredField,
  CalculationCheck,
  CalculationOperator,
  OverridableType,
  SchemaField,
  SessionCreate,
  SessionMode,
  UploadResult,
} from "../api/types";
import { Button, Card, CardHeader, ErrorState } from "../components/ui";
import { cn, formatNumber } from "../lib";

const ACCEPTED = [".csv", ".xlsx", ".xls", ".ods"];
const SPREADSHEETS = new Set([".xlsx", ".xls", ".ods"]);
const MAX_FILES = 20;
const MAX_MB = 50;

const TYPE_LABELS: Record<string, string> = { text: "Texte", decimal: "Nombre", date: "Date", enum: "Liste" };

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} Ko` : `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
}

// --------------------------------------------------------------------------- étapes

function Steps({ current }: { current: 1 | 2 }) {
  const steps = ["Fichiers", "Vérification et configuration", "Création de la session"];
  return (
    <ol className="mb-6 flex flex-wrap items-center gap-2 text-[13px]" aria-label="Étapes de l'import">
      {steps.map((label, index) => {
        const step = index + 1;
        const done = step < current;
        const active = step === current;
        return (
          <li key={label} className="flex items-center gap-2" aria-current={active ? "step" : undefined}>
            <span
              className={cn(
                "grid size-6 place-items-center rounded-full text-xs font-semibold",
                done ? "bg-success-bg text-success" : active ? "bg-primary text-white" : "bg-subtle text-ink-3",
              )}
            >
              {done ? <Check aria-hidden className="size-3.5" /> : step}
            </span>
            <span className={cn(active ? "font-medium text-ink" : "text-ink-3")}>{label}</span>
            {step < steps.length ? <span aria-hidden className="mx-1 h-px w-8 bg-line" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

// --------------------------------------------------------------------------- étape 1 : dépôt

function Dropzone({
  files,
  onChange,
  onSubmit,
  pending,
}: {
  files: File[];
  onChange: (files: File[]) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const [refused, setRefused] = useState<string[]>([]);

  function add(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    const bad = incoming.filter((f) => !ACCEPTED.includes(extension(f.name)) || f.size > MAX_MB * 1024 * 1024);
    setRefused(
      bad.map((f) =>
        ACCEPTED.includes(extension(f.name)) ? `${f.name} : plus de ${MAX_MB} Mo` : `${f.name} : format non accepté (CSV, XLSX, XLS ou ODS)`,
      ),
    );
    const known = new Set(files.map((f) => f.name));
    const good = incoming.filter((f) => !bad.includes(f) && !known.has(f.name));
    onChange([...files, ...good].slice(0, MAX_FILES));
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    add(event.dataTransfer.files);
  }

  return (
    <Card>
      <div className="p-5.5">
        <label
          htmlFor={inputId}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors",
            dragging ? "border-primary bg-brand-50" : "border-line hover:border-line-strong hover:bg-app",
          )}
        >
          <span className="grid size-12 place-items-center rounded-full bg-brand-50 text-primary">
            <Upload aria-hidden className="size-5" />
          </span>
          <span className="text-base font-semibold">Glissez vos fichiers ici, ou cliquez pour les choisir</span>
          <span className="max-w-md text-[13px] text-ink-2">
            CSV, Excel (.xlsx, .xls) ou OpenDocument (.ods), {MAX_FILES} fichiers et {MAX_MB} Mo au plus chacun. Opérations bancaires ou n'importe quel
            tableau : factures, RH, stocks, ventes…
          </span>
          <input
            id={inputId}
            ref={input}
            type="file"
            multiple
            accept={ACCEPTED.join(",")}
            className="sr-only"
            onChange={(event) => {
              add(event.target.files);
              event.target.value = "";
            }}
          />
        </label>

        {refused.length ? (
          <ul role="alert" className="mt-4 rounded-md bg-warning-bg px-4 py-3 text-[13px] text-warning">
            {refused.map((r) => (
              <li key={r} className="flex items-center gap-2">
                <AlertTriangle aria-hidden className="size-4 shrink-0" />
                {r}
              </li>
            ))}
          </ul>
        ) : null}

        {files.length ? (
          <ul className="mt-4 divide-y divide-line-subtle rounded-lg border border-line" aria-label="Fichiers sélectionnés">
            {files.map((file) => (
              <li key={file.name} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                {SPREADSHEETS.has(extension(file.name)) ? (
                  <FileSpreadsheet aria-hidden className="size-4 text-primary" />
                ) : (
                  <FileText aria-hidden className="size-4 text-primary" />
                )}
                <span className="flex-1 font-medium">{file.name}</span>
                <span className="text-ink-3 tabular-nums">{formatSize(file.size)}</span>
                <button
                  type="button"
                  aria-label={`Retirer ${file.name}`}
                  onClick={() => onChange(files.filter((f) => f !== file))}
                  className="grid size-7 cursor-pointer place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-ink"
                >
                  <X aria-hidden className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="mt-5 flex items-center justify-end gap-2">
          {files.length ? (
            <Button variant="ghost" icon={Trash2} onClick={() => onChange([])} disabled={pending}>
              Tout retirer
            </Button>
          ) : null}
          <Button variant="primary" disabled={!files.length || pending} onClick={onSubmit} aria-busy={pending}>
            {pending ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
            {pending ? "Lecture des fichiers…" : `Analyser ${files.length > 1 ? `les ${files.length} fichiers` : "le fichier"}`}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- étape 2 : rapport par fichier

function FileReport({ file }: { file: FileInspection }) {
  const format =
    file.kind !== "csv"
      ? `${SHEET_FORMATS[file.kind]}, feuille « ${file.sheet ?? "?"} »`
      : `CSV, ${file.encoding ?? "?"}, séparateur « ${file.delimiter === "\t" ? "tabulation" : file.delimiter ?? "?"} »`;
  return (
    <Card>
      <CardHeader title={file.file} hint={`${format} · ${formatNumber(file.rows)} lignes`} />
      <div className="grid gap-3 px-5.5 pt-3 pb-5">
        {file.rejected_count || file.empty_columns.length || file.warnings.length ? (
          <ul className="grid gap-1 rounded-md bg-warning-bg px-3.5 py-2.5 text-[13px] text-warning">
            {file.rejected_count ? (
              <li>
                {file.rejected_count} ligne(s) mal formée(s) mise(s) de côté
                {file.rejected_rows[0] ? ` (ex. ligne ${file.rejected_rows[0].row} : ${file.rejected_rows[0].reason})` : ""}.
              </li>
            ) : null}
            {file.empty_columns.length ? <li>Colonnes entièrement vides : {file.empty_columns.join(", ")}.</li> : null}
            {file.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
        <div className="overflow-x-auto rounded-md border border-line-subtle">
          <table className="w-full border-collapse text-[12px]">
            <caption className="sr-only">Aperçu des premières lignes de {file.file}</caption>
            <thead>
              <tr>
                {file.headers.map((h) => (
                  <th key={h} scope="col" className="border-b border-line-subtle bg-subtle px-2.5 py-1.5 text-left font-medium whitespace-nowrap text-ink-2">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {file.preview.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} className="max-w-56 truncate border-b border-line-subtle px-2.5 py-1.5 whitespace-nowrap last:border-b-0">
                      {cell ?? <span className="text-ink-3">—</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-ink-3">Aperçu des {file.preview.length} premières lignes, valeurs telles que lues.</p>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- choix du mode

function ModeChoice({
  result,
  mode,
  onChange,
}: {
  result: UploadResult;
  mode: SessionMode;
  onChange: (mode: SessionMode) => void;
}) {
  const missing = [...new Set(result.files.flatMap((f) => f.missing_required))];
  const options: { value: SessionMode; title: string; text: string; disabled: boolean }[] = [
    {
      value: "generic",
      title: "Générique",
      text: "Tout tableau : schéma déduit des colonnes, vous désignez la clé et les champs obligatoires.",
      disabled: false,
    },
    {
      value: "banking",
      title: "Opérations bancaires",
      text: missing.length
        ? `Indisponible : champs bancaires absents (${missing.join(", ")}).`
        : "Schéma bancaire, noms similaires, clients multi-banques et règles bancaires.",
      disabled: missing.length > 0,
    },
  ];
  return (
    <fieldset className="grid min-w-0 grid-cols-2 gap-3">
      <legend className="mb-2.5 text-sm font-semibold">Mode d'analyse</legend>
      {options.map((o) => (
        <label
          key={o.value}
          className={cn(
            "flex items-start gap-2.5 rounded-lg border p-3.5 text-[13px] transition-colors",
            o.disabled ? "cursor-not-allowed border-line bg-app text-ink-3" : "cursor-pointer border-line hover:border-line-strong",
            mode === o.value && !o.disabled ? "border-primary bg-brand-50 shadow-[inset_0_0_0_1px_var(--primary)]" : "",
          )}
        >
          <input
            type="radio"
            name="mode"
            value={o.value}
            checked={mode === o.value}
            disabled={o.disabled}
            onChange={() => onChange(o.value)}
            className="mt-0.5 accent-[var(--primary)]"
          />
          <span>
            <span className="flex items-center gap-2 font-medium text-ink">
              {o.title}
              {result.suggested_mode === o.value ? (
                <span className="rounded-full bg-success-bg px-2 text-[11px] font-medium text-success">suggéré</span>
              ) : null}
            </span>
            <span className="mt-0.5 block text-xs text-ink-3">{o.text}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

// --------------------------------------------------------------------------- mode générique

const SHEET_FORMATS: Record<Exclude<FileInspection["kind"], "csv">, string> = {
  xlsx: "Excel",
  xls: "Excel 97-2003",
  ods: "OpenDocument",
};

const TYPE_CHOICES: OverridableType[] = ["text", "decimal", "date"];

interface GenericOptions {
  key: Set<string>;
  required: Set<string>;
  groupBy: Set<string>;
  /** Types choisis par l'utilisateur, seulement là où ils diffèrent du type détecté. */
  types: Record<string, OverridableType>;
  /** Contrôles de calcul déclarés, sur des colonnes de type nombre. */
  checks: CalculationCheck[];
}

const NO_OPTIONS: GenericOptions = { key: new Set(), required: new Set(), groupBy: new Set(), types: {}, checks: [] };

const OPERATORS: { value: CalculationOperator; sign: string; label: string }[] = [
  { value: "*", sign: "×", label: "multiplié par" },
  { value: "+", sign: "+", label: "plus" },
  { value: "-", sign: "−", label: "moins" },
  { value: "/", sign: "÷", label: "divisé par" },
];

/** Change le type d'une colonne ; une colonne qui n'est plus du texte ne peut plus servir de groupe. */
function withType(options: GenericOptions, field: InferredField, type: OverridableType): GenericOptions {
  const types = { ...options.types };
  if (type === field.type) delete types[field.name];
  else types[field.name] = type;
  const groupBy = new Set(options.groupBy);
  if (type !== "text") groupBy.delete(field.name);
  // Un contrôle de calcul ne porte que sur des nombres : il disparaît si une de ses colonnes n'en est plus.
  const checks =
    type === "decimal"
      ? options.checks
      : options.checks.filter((c) => ![c.result, c.left, c.right].includes(field.name));
  return { ...options, types, groupBy, checks };
}

function toggle(set: Set<string>, value: string): Set<string> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

function GenericEditor({
  fields,
  options,
  onChange,
}: {
  fields: InferredField[];
  options: GenericOptions;
  onChange: (options: GenericOptions) => void;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold">Schéma déduit des fichiers</h3>
      <p className="mt-1 mb-3 text-[13px] text-ink-2">
        Désignez la <b className="font-medium text-ink">clé d'unicité</b> (un identifiant qui ne devrait jamais se répéter),
        les <b className="font-medium text-ink">champs obligatoires</b> et, si utile, une colonne pour comparer les
        valeurs numériques par groupe. Corrigez le <b className="font-medium text-ink">type</b> si la détection se trompe :
        les valeurs qui ne s'y conforment pas seront signalées, jamais supprimées.
      </p>
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="text-left text-xs text-ink-3">
              <th scope="col" className="bg-subtle px-3 py-2 font-medium">Colonne</th>
              <th scope="col" className="bg-subtle px-3 py-2 font-medium">Type</th>
              <th scope="col" className="bg-subtle px-3 py-2 text-right font-medium">Valeurs</th>
              <th scope="col" className="bg-subtle px-3 py-2 text-center font-medium">Clé</th>
              <th scope="col" className="bg-subtle px-3 py-2 text-center font-medium">Obligatoire</th>
              <th scope="col" className="bg-subtle px-3 py-2 text-center font-medium">Grouper par</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => {
              const type = options.types[f.name] ?? f.type;
              const changed = type !== f.type;
              return (
                <tr key={f.name} className="border-t border-line-subtle">
                  <th scope="row" className="px-3 py-2 text-left font-medium">
                    {f.label}
                    <span className="ml-2 font-mono text-[11px] font-normal text-ink-3">{f.name}</span>
                  </th>
                  <td className="min-w-48 px-3 py-2">
                    <select
                      aria-label={`${f.label} : type`}
                      value={type}
                      onChange={(event) => onChange(withType(options, f, event.target.value as OverridableType))}
                      className={cn("h-7 rounded-md border bg-surface px-1.5 text-xs", changed ? "border-primary text-primary" : "border-line")}
                    >
                      {TYPE_CHOICES.map((t) => (
                        <option key={t} value={t}>
                          {TYPE_LABELS[t]}
                        </option>
                      ))}
                    </select>
                    {changed ? (
                      <span className="mt-1 block text-xs text-ink-3">détecté : {(TYPE_LABELS[f.type] ?? f.type).toLowerCase()}</span>
                    ) : f.reason !== "texte" ? (
                      <span className="mt-1 block text-xs text-ink-3">{f.reason}</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.filled)}</td>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${f.label} : clé d'unicité`}
                      checked={options.key.has(f.name)}
                      onChange={() => onChange({ ...options, key: toggle(options.key, f.name) })}
                      className="accent-[var(--primary)]"
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${f.label} : obligatoire`}
                      checked={options.required.has(f.name)}
                      onChange={() => onChange({ ...options, required: toggle(options.required, f.name) })}
                      className="accent-[var(--primary)]"
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    {type === "text" ? (
                      <input
                        type="checkbox"
                        aria-label={`${f.label} : grouper les valeurs numériques`}
                        checked={options.groupBy.has(f.name)}
                        onChange={() => onChange({ ...options, groupBy: toggle(options.groupBy, f.name) })}
                        className="accent-[var(--primary)]"
                      />
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!options.key.size ? (
        <p className="mt-2.5 text-xs text-ink-3">
          Sans clé, seules les lignes entièrement identiques seront recherchées comme doublons.
        </p>
      ) : null}
      <CalculationChecks fields={fields} options={options} onChange={onChange} />
    </div>
  );
}

function CalculationChecks({
  fields,
  options,
  onChange,
}: {
  fields: InferredField[];
  options: GenericOptions;
  onChange: (options: GenericOptions) => void;
}) {
  const numeric = fields.filter((f) => (options.types[f.name] ?? f.type) === "decimal");
  const select = "h-8 rounded-md border border-line bg-surface px-1.5 text-[13px]";
  const update = (index: number, patch: Partial<CalculationCheck>) =>
    onChange({ ...options, checks: options.checks.map((c, i) => (i === index ? { ...c, ...patch } : c)) });
  const columnSelect = (index: number, key: "result" | "left" | "right", label: string) => (
    <select
      aria-label={`Contrôle ${index + 1} : ${label}`}
      value={options.checks[index]?.[key] ?? ""}
      onChange={(event) => update(index, { [key]: event.target.value })}
      className={cn(select, "max-w-44")}
    >
      {numeric.map((f) => (
        <option key={f.name} value={f.name}>
          {f.label}
        </option>
      ))}
    </select>
  );

  return (
    <div className="mt-5">
      <h3 className="text-sm font-semibold">Contrôles de calcul</h3>
      <p className="mt-1 mb-3 text-[13px] text-ink-2">
        Facultatif. Vérifie qu'une colonne vaut le calcul de deux autres, par exemple « Total = Quantité × Prix
        unitaire ». Une ligne où une valeur manque n'est pas jugée ; un écart au-delà de la tolérance devient un
        constat à examiner.
      </p>
      {options.checks.length ? (
        <ul className="grid gap-2" aria-label="Contrôles de calcul">
          {options.checks.map((check, index) => (
            <li key={index} className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-app px-3 py-2 text-[13px]">
              {columnSelect(index, "result", "résultat")}
              <span aria-hidden>=</span>
              {columnSelect(index, "left", "première colonne")}
              <select
                aria-label={`Contrôle ${index + 1} : opération`}
                value={check.operator}
                onChange={(event) => update(index, { operator: event.target.value as CalculationOperator })}
                className={select}
              >
                {OPERATORS.map((o) => (
                  <option key={o.value} value={o.value} title={o.label}>
                    {o.sign}
                  </option>
                ))}
              </select>
              {columnSelect(index, "right", "seconde colonne")}
              <label className="flex items-center gap-1.5 text-ink-2">
                tolérance ±
                <input
                  aria-label={`Contrôle ${index + 1} : tolérance`}
                  value={check.tolerance}
                  inputMode="decimal"
                  onChange={(event) => update(index, { tolerance: event.target.value })}
                  className="h-8 w-20 rounded-md border border-line bg-surface px-2 text-[13px]"
                />
              </label>
              <button
                type="button"
                aria-label={`Retirer le contrôle ${index + 1}`}
                onClick={() => onChange({ ...options, checks: options.checks.filter((_, i) => i !== index) })}
                className="ml-auto grid size-7 cursor-pointer place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-ink"
              >
                <X aria-hidden className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {numeric.length >= 2 ? (
        <Button
          className="mt-2 h-8"
          icon={Plus}
          onClick={() => {
            const [a, b, c] = [numeric[0], numeric[1], numeric[2] ?? numeric[0]];
            if (!a || !b || !c) return;
            onChange({
              ...options,
              checks: [...options.checks, { result: c.name, left: a.name, operator: "*", right: b.name, tolerance: "0,01" }],
            });
          }}
        >
          Ajouter un contrôle
        </Button>
      ) : (
        <p className="text-xs text-ink-3">Il faut au moins deux colonnes de type nombre pour déclarer un calcul.</p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- mode bancaire

/** Champ retenu pour une colonne : le choix de l'utilisateur s'il existe (y compris « aucun »,
 * qui vaut null), sinon la proposition du moteur. `??` confondrait null et « pas de choix ». */
function effectiveTarget(
  overrides: Record<string, Record<string, string | null>>,
  file: string,
  source: string,
  proposed: string | null,
): string | null {
  const chosen = overrides[file];
  return chosen && Object.hasOwn(chosen, source) ? (chosen[source] ?? null) : proposed;
}

function BankingEditor({
  files,
  schema,
  overrides,
  onChange,
}: {
  files: FileInspection[];
  schema: SchemaField[];
  overrides: Record<string, Record<string, string | null>>;
  onChange: (overrides: Record<string, Record<string, string | null>>) => void;
}) {
  return (
    <div className="grid gap-4">
      <div>
        <h3 className="text-sm font-semibold">Correspondance des colonnes</h3>
        <p className="mt-1 text-[13px] text-ink-2">
          Vérifiez le champ associé à chaque colonne ; les propositions « à vérifier » reposent sur une ressemblance de nom.
        </p>
      </div>
      {files.map((file) => (
        <div key={file.file} className="overflow-x-auto rounded-md border border-line">
          <table className="w-full border-collapse text-[13px]">
            <caption className="bg-subtle px-3 py-2 text-left text-xs font-medium text-ink-2">{file.file}</caption>
            <tbody>
              {file.proposals.map((p) => {
                const current = effectiveTarget(overrides, file.file, p.source, p.target);
                const doubtful = p.method === "similarity";
                return (
                  <tr key={p.source} className="border-t border-line-subtle">
                    <th scope="row" className="w-1/3 px-3 py-1.5 text-left font-medium">{p.source}</th>
                    <td className="px-3 py-1.5">
                      <select
                        aria-label={`Champ associé à ${p.source}`}
                        value={current ?? ""}
                        onChange={(event) =>
                          onChange({
                            ...overrides,
                            [file.file]: { ...overrides[file.file], [p.source]: event.target.value || null },
                          })
                        }
                        className="h-8 w-64 rounded-md border border-line bg-surface px-2 text-[13px]"
                      >
                        <option value="">(non associée)</option>
                        {schema.map((f) => (
                          <option key={f.name} value={f.name}>
                            {f.label}
                            {f.required ? " *" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-1.5 text-xs text-ink-3">
                      {doubtful ? <span className="text-warning">à vérifier ({Math.round(100 * p.confidence)} %)</span> : p.note}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
      <p className="text-xs text-ink-3">* champ obligatoire du schéma bancaire.</p>
    </div>
  );
}

// --------------------------------------------------------------------------- page

export function ImportPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<SessionMode>("generic");
  const [name, setName] = useState("");
  const [options, setOptions] = useState<GenericOptions>(NO_OPTIONS);
  const [overrides, setOverrides] = useState<Record<string, Record<string, string | null>>>({});
  const schema = useQuery({ queryKey: ["schema"], queryFn: api.schema, staleTime: Infinity });

  const upload = useMutation({
    mutationFn: () => api.upload(files),
    onSuccess: (result) => {
      setMode(result.suggested_mode);
      setName(result.files.map((f) => f.file).join(", "));
      setOptions(NO_OPTIONS);
      setOverrides({});
    },
  });

  const create = useMutation({
    mutationFn: (payload: SessionCreate) => api.createSession(payload),
    onSuccess: async (session) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      void navigate(`/sessions/${session.session_id}`);
    },
  });

  const result = upload.data;
  const missingAfterOverrides = useMemo(() => {
    if (!result || !schema.data) return [];
    const required = schema.data.filter((f) => f.required).map((f) => f.name);
    return result.files.flatMap((file) => {
      const mapped = new Set(file.proposals.map((p) => effectiveTarget(overrides, file.file, p.source, p.target)).filter(Boolean));
      return required.filter((r) => !mapped.has(r)).map((r) => `${file.file} : ${r}`);
    });
  }, [result, schema.data, overrides]);

  function submit() {
    if (!result) return;
    const base = { upload_id: result.upload_id, mode, name: name.trim() || result.files.map((f) => f.file).join(", ") };
    create.mutate(
      mode === "generic"
        ? {
            ...base,
            key_fields: [...options.key],
            required_fields: [...options.required],
            group_by: [...options.groupBy],
            ...(Object.keys(options.types).length ? { types: options.types } : {}),
            ...(options.checks.length
              ? { checks: options.checks.map((c) => ({ ...c, tolerance: c.tolerance.trim().replace(",", ".") || "0" })) }
              : {}),
          }
        : { ...base, mappings: overrides },
    );
  }

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Nouvel import</h1>
        <p className="mt-1.5 text-ink-2">
          Les fichiers sont lus sans être modifiés ; rien n'est enregistré avant la création de la session.
        </p>
      </div>
      <Steps current={result ? 2 : 1} />

      {!result ? (
        <>
          {upload.isError ? (
            <div className="mb-4">
              <ErrorState error={upload.error} />
            </div>
          ) : null}
          <Dropzone files={files} onChange={setFiles} onSubmit={() => upload.mutate()} pending={upload.isPending} />
        </>
      ) : (
        <div className="grid gap-5">
          {result.files.map((file) => (
            <FileReport key={file.file} file={file} />
          ))}

          <Card>
            <CardHeader title="Configuration de la session" />
            <div className="grid grid-cols-[minmax(0,1fr)] gap-5 px-5.5 pt-4 pb-5.5">
              <ModeChoice result={result} mode={mode} onChange={setMode} />
              {mode === "generic" ? (
                <GenericEditor fields={result.generic_fields} options={options} onChange={setOptions} />
              ) : schema.data ? (
                <BankingEditor files={result.files} schema={schema.data} overrides={overrides} onChange={setOverrides} />
              ) : null}

              <label className="grid max-w-xl gap-1.5 text-sm font-semibold">
                Nom de la session
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={200}
                  className="h-9 rounded-md border border-line bg-surface px-3 text-[13px] font-normal"
                />
              </label>

              {mode === "banking" && missingAfterOverrides.length ? (
                <p role="alert" className="rounded-md bg-danger-bg px-3.5 py-2.5 text-[13px] text-danger">
                  Champs obligatoires sans colonne : {missingAfterOverrides.join(" ; ")}.
                </p>
              ) : null}
              {create.isError ? <ErrorState error={create.error} /> : null}

              <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle pt-4">
                <span className="mr-auto min-w-56 flex-1 text-xs text-ink-3">
                  La création enregistre un instantané en lecture seule des données ; elles ne seront jamais modifiées.
                </span>
                <Button
                  variant="secondary"
                  disabled={create.isPending}
                  onClick={() => {
                    upload.reset();
                    create.reset();
                  }}
                >
                  Changer de fichiers
                </Button>
                <Button
                  variant="primary"
                  disabled={create.isPending || (mode === "banking" && missingAfterOverrides.length > 0)}
                  onClick={submit}
                  aria-busy={create.isPending}
                >
                  {create.isPending ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : <Check aria-hidden className="size-4" />}
                  {create.isPending ? "Création…" : "Créer la session"}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
