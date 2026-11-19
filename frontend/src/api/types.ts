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
