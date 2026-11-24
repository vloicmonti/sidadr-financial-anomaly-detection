// Types des réponses de l'API locale (src/sidadr/api/app.py).

export type Severity = "certain_error" | "probable_inconsistency" | "atypical_value" | "review_alert";
export type FindingStatus = "open" | "confirmed" | "rejected" | "kept_both" | "deferred";

export interface SourceImport {
  import_id: string;
  source_file: string;
  rows: number;
  rows_rejected: number;
  mapping: Record<string, string | null>;
}

export interface SessionSummary {
  session_id: string;
  name: string;
  created_at: string;
  rows: number;
  original_sha256: string;
  imports: SourceImport[];
}

export interface ScoreDimension {
  name: string;
  label: string;
  definition: string;
  weight: number;
  effective_weight: number;
  value: number | null;
  numerator: number;
  denominator: number;
  note: string;
}

export interface QualityScore {
  score: number;
  dimensions: ScoreDimension[];
  notes: string[];
}

export interface RuleOutcome {
  rule_id: string;
  description: string;
  severity: Severity;
  dimension: string;
  checked: number;
  violations: number;
  not_evaluable: number;
}

export interface AnalysisSummary {
  generated_at: string;
  sidadr_version: string;
  rows_analyzed: number;
  mode?: SessionMode;
  score: QualityScore;
  /** Absent des analyses antérieures à l'écran « Qualité des données ». */
  profile?: DataProfile;
  findings: {
    total: number;
    by_severity: Partial<Record<Severity, number>>;
    by_kind: Record<string, number>;
    by_status: Partial<Record<FindingStatus, number>>;
  };
  rules: RuleOutcome[];
  rule_parameters: Record<string, string>;
}

export interface SessionState extends SessionSummary {
  mode?: SessionMode;
  schema?: SchemaField[];
  working_rows: number;
  excluded_records: number;
  active_corrections: number;
  active_decisions: number;
  journal_entries: number;
  journal_integrity: string;
  analysis: AnalysisSummary | null;
  analysis_up_to_date: boolean;
}

export interface Health {
  status: string;
  version: string;
}

export type FieldType = "text" | "decimal" | "date" | "enum";
export type SessionMode = "banking" | "generic";

export interface SchemaField {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  values: string[];
}

export interface MappingProposal {
  source: string;
  target: string | null;
  confidence: number;
  method: "synonym" | "similarity" | "manual" | "identity" | "none";
  note: string;
}

export interface FileInspection {
  file: string;
  kind: "csv" | "xlsx" | "xls" | "ods";
  encoding: string | null;
  delimiter: string | null;
  sheet: string | null;
  rows: number;
  rejected_rows: { row: number; reason: string }[];
  rejected_count: number;
  empty_columns: string[];
  warnings: string[];
  headers: string[];
  preview: (string | null)[][];
  proposals: MappingProposal[];
  missing_required: string[];
}

export interface InferredField {
  name: string;
  label: string;
  type: FieldType;
  filled: number;
  conformity: number;
  sources: string[];
  reason: string;
}

export interface UploadResult {
  upload_id: string;
  files: FileInspection[];
  suggested_mode: SessionMode;
  generic_fields: InferredField[];
}

export interface SessionCreate {
  upload_id: string;
  name?: string;
  mode: SessionMode;
  mappings?: Record<string, Record<string, string | null>>;
  key_fields?: string[];
  required_fields?: string[];
  group_by?: string[];
  /** Types imposés en mode générique, par nom de champ. */
  types?: Record<string, OverridableType>;
}

/** Types qu'on peut imposer à une colonne à l'import (une liste de valeurs exigerait ses valeurs). */
export type OverridableType = "text" | "decimal" | "date";

export interface Finding {
  finding_id: string;
  kind: string;
  severity: Severity;
  record_ids: string[];
  rule_id: string;
  explanation: string;
  recommendation: string;
  field: string | null;
  observed_value: string | null;
  threshold: string | null;
  score: number | null;
  key: string;
  status: FindingStatus;
}

/** Une ligne de données : colonnes du schéma de la session, plus les colonnes techniques. */
export interface RecordRow {
  record_id: string;
  [column: string]: unknown;
}

export interface FindingDetail extends Finding {
  records: (RecordRow & { excluded: boolean })[];
}

export interface FindingPage {
  total: number;
  page: number;
  page_size: number;
  items: Finding[];
  facets: { severity: Record<string, number>; kind: Record<string, number>; status: Record<string, number> };
}

export interface FindingQuery {
  severity?: Severity | undefined;
  kind?: string | undefined;
  status?: FindingStatus | undefined;
  q?: string | undefined;
  page?: number | undefined;
  page_size?: number | undefined;
}

export type DecisionValue = Exclude<FindingStatus, "open">;

export interface RecordQuery {
  q?: string | undefined;
  include_excluded?: boolean | undefined;
  page?: number | undefined;
  page_size?: number | undefined;
}

export interface RecordListItem extends RecordRow {
  excluded: boolean;
  corrected_fields: string[];
}

export interface RecordPage {
  total: number;
  page: number;
  page_size: number;
  items: RecordListItem[];
}

export interface Correction {
  id: number;
  record_id: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  reason: string;
  actor: string;
  created_at: string;
  active: 0 | 1;
}

export interface RecordDetail {
  record_id: string;
  original: RecordRow;
  working: RecordRow;
  excluded: boolean;
  corrections: Correction[];
  findings: Finding[];
}

export type AuditAction = "correct" | "exclude" | "decide" | "undo";

export interface AuditEntry {
  entry_id: number;
  ts_utc: string;
  actor: string;
  action_type: AuditAction;
  target: string | null;
  record_id: string | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  finding_id: string | null;
}

export interface History {
  entries: AuditEntry[];
  integrity: { entries: number; valid: boolean; first_broken_entry: number | null };
}

export interface ColumnProfile {
  name: string;
  label: string;
  type: string;
  required: boolean;
  rows: number;
  missing: number;
  distinct: number;
  invalid: number;
  checked_for_validity: boolean;
  invalid_reasons: Record<string, number>;
  invalid_samples: string[];
  decisions: string[];
  numeric: { count: number; min: string; q1: string; median: string; q3: string; max: string; mean: number; std: number } | null;
  date_range: [string, string] | null;
  top_values: [string, number][];
}

export interface DataProfile {
  rows: number;
  duplicate_rows: number;
  duplicate_groups: number;
  columns: ColumnProfile[];
}
