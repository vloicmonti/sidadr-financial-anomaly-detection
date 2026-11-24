import type {
  AnalysisSummary,
  DecisionValue,
  FindingDetail,
  FindingPage,
  FindingQuery,
  Health,
  History,
  RecordDetail,
  RecordPage,
  RecordQuery,
  SchemaField,
  SessionCreate,
  SessionState,
  SessionSummary,
  UploadResult,
} from "./types";

/** Erreur renvoyée par l'API, avec le message lisible prévu pour l'utilisateur. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const NETWORK_MESSAGE =
  "Impossible de joindre le moteur SIDADR. Vérifiez qu'il est démarré (sidadr serve).";

async function readError(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const { detail, code } = body as { detail: unknown; code?: unknown };
      const message =
        typeof detail === "string"
          ? detail
          : "La requête a été refusée : certaines valeurs sont invalides.";
      return new ApiError(response.status, message, typeof code === "string" ? code : null);
    }
  } catch {
    // corps non JSON : message générique ci-dessous
  }
  return new ApiError(response.status, `Erreur inattendue du moteur (HTTP ${response.status}).`);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      // FormData : le navigateur fixe lui-même le Content-Type (avec la frontière multipart).
      headers: { Accept: "application/json", ...(typeof init?.body === "string" ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    });
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE, "network");
  }
  if (!response.ok) throw await readError(response);
  return (await response.json()) as T;
}

function query(values: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "" && value !== false) params.set(key, String(value));
  }
  return params.toString();
}

const session = (id: string) => `/sessions/${encodeURIComponent(id)}`;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  sessions: () => request<SessionSummary[]>("/sessions"),
  session: (id: string) => request<SessionState>(`/sessions/${encodeURIComponent(id)}`),
  analyze: (id: string) =>
    request<AnalysisSummary>(`/sessions/${encodeURIComponent(id)}/analyze`, { method: "POST" }),
  schema: () => request<SchemaField[]>("/schema"),
  upload: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    return request<UploadResult>("/imports", { method: "POST", body: form });
  },
  createSession: (payload: SessionCreate) =>
    request<SessionState>("/sessions", { method: "POST", body: JSON.stringify(payload) }),
  findings: (id: string, filters: FindingQuery) => request<FindingPage>(`${session(id)}/findings?${query(filters)}`),
  finding: (id: string, findingId: string) =>
    request<FindingDetail>(`/sessions/${encodeURIComponent(id)}/findings/${encodeURIComponent(findingId)}`),
  decide: (id: string, findingId: string, decision: DecisionValue, reason: string) =>
    request<{ target: string }>(
      `/sessions/${encodeURIComponent(id)}/findings/${encodeURIComponent(findingId)}/decision`,
      post({ decision, reason }),
    ),
  records: async (id: string, filters: RecordQuery): Promise<RecordPage> => {
    const page = await request<RecordPage>(`${session(id)}/records?${query(filters)}`);
    // Un moteur lancé avant ces indicateurs ne les renvoie pas : valeurs neutres plutôt qu'un plantage.
    return {
      ...page,
      items: page.items.map((item) => ({
        ...item,
        excluded: item.excluded ?? false,
        corrected_fields: item.corrected_fields ?? [],
      })),
    };
  },
  record: (id: string, recordId: string) =>
    request<RecordDetail>(`${session(id)}/records/${encodeURIComponent(recordId)}`),
  correct: (id: string, recordId: string, field: string, value: string | null, reason: string) =>
    request<{ target: string; old_value: string | null; new_value: string | null }>(
      `${session(id)}/corrections`,
      post({ record_id: recordId, field, value, reason }),
    ),
  exclude: (id: string, recordId: string, reason: string) =>
    request<{ target: string }>(`${session(id)}/exclusions`, post({ record_id: recordId, reason })),
  undo: (id: string, target: string, reason: string) =>
    request<{ target: string }>(`${session(id)}/undo`, post({ target, reason })),
  history: (id: string) => request<History>(`${session(id)}/history`),
};
