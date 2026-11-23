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
  journal_entries_at_analysis: number;
  score: QualityScore;
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
  kind: "csv" | "xlsx";
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
}
