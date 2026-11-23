import type { DecisionValue, FindingStatus } from "./api/types";

/** Vues de l'écran des constats, utilisées aussi par la barre latérale. */
export const FINDING_VIEWS = {
  all: { label: "Tous les constats", kinds: undefined },
  duplicates: {
    label: "Doublons",
    kinds: "exact_duplicate_row,duplicate_key,cross_file_duplicate,combination_match",
  },
  identities: { label: "Noms & identités", kinds: "name_match,multi_bank,multi_branch" },
  anomalies: { label: "Anomalies", kinds: "atypical_value" },
  rules: { label: "Règles métier", kinds: "rule_violation" },
} as const satisfies Record<string, { label: string; kinds: string | undefined }>;

export type FindingView = keyof typeof FINDING_VIEWS;

export function isFindingView(value: string | undefined): value is FindingView {
  return value !== undefined && value in FINDING_VIEWS;
}

export const KIND_LABELS: Record<string, string> = {
  rule_violation: "Règle métier",
  exact_duplicate_row: "Lignes identiques",
  duplicate_key: "Identifiant en double",
  cross_file_duplicate: "Doublon inter-fichiers",
  combination_match: "Combinaison identique",
  atypical_value: "Valeur atypique",
  name_match: "Noms similaires",
  multi_bank: "Client multi-banques",
  multi_branch: "Client multi-agences",
};

export const STATUS_LABELS: Record<FindingStatus, string> = {
  open: "Ouvert",
  confirmed: "Confirmé",
  rejected: "Rejeté",
  kept_both: "Conservés",
  deferred: "Différé",
};

export const DECISIONS: { value: DecisionValue; label: string; help: string }[] = [
  { value: "confirmed", label: "Confirmer", help: "Le constat est fondé." },
  { value: "rejected", label: "Rejeter", help: "Faux positif : rien à corriger." },
  { value: "kept_both", label: "Conserver les deux", help: "Correspondance réelle, mais les enregistrements restent séparés." },
  { value: "deferred", label: "Différer", help: "À revoir plus tard." },
];

/** Types de constats dont les enregistrements se lisent mieux regroupés par fiche client. */
export const CUSTOMER_KINDS = new Set(["name_match", "multi_bank", "multi_branch"]);

/** Champs qui décrivent la fiche client (et non l'opération) dans le schéma bancaire. */
export const CUSTOMER_FIELDS = new Set([
  "bank_code",
  "branch_code",
  "branch_city",
  "customer_id",
  "customer_name",
  "birth_date",
  "phone",
  "account_number",
]);
