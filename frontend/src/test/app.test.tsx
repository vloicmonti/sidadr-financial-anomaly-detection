import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, request } from "../api/client";
import type { SessionState, SessionSummary } from "../api/types";
import { AppShell } from "../components/AppShell";
import { OverviewPage } from "../pages/OverviewPage";
import { SessionsPage } from "../pages/SessionsPage";

const SUMMARY: SessionSummary = {
  session_id: "S-20261119-120000-abc123",
  name: "Contrôle T2",
  created_at: "2026-11-19T12:00:00+00:00",
  rows: 20200,
  original_sha256: "0".repeat(64),
  imports: [{ import_id: "alpha_t2", source_file: "alpha_T2.csv", rows: 8154, rows_rejected: 0, mapping: {} }],
};

const STATE: SessionState = {
  ...SUMMARY,
  working_rows: 20199,
  excluded_records: 1,
  active_corrections: 2,
  active_decisions: 3,
  journal_entries: 6,
  journal_integrity: "intact",
  analysis: null,
  analysis_up_to_date: false,
};

type Routes = Record<string, unknown | ((init?: RequestInit) => unknown)>;

function mockApi(routes: Routes) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = Object.keys(routes).find((path) => url === `/api${path}`);
    if (key === undefined) return new Response(JSON.stringify({ detail: "Route inconnue" }), { status: 404 });
    const value = routes[key];
    const body = typeof value === "function" ? (value as (i?: RequestInit) => unknown)(init) : value;
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: "/", element: <SessionsPage /> },
          { path: "/sessions/:sessionId", element: <OverviewPage /> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("client API", () => {
  it("transmet le message lisible de l'API", async () => {
    mockApi({ "/sessions/x": new Response(JSON.stringify({ detail: "Session introuvable : x", code: "session" }), { status: 404 }) });
    await expect(request("/sessions/x")).rejects.toMatchObject({ status: 404, message: "Session introuvable : x", code: "session" });
  });

  it("explique quoi faire quand le moteur est arrêté", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await request("/health").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toContain("sidadr serve");
  });
});

describe("page Sessions", () => {
  it("liste les sessions et signale le moteur connecté", async () => {
    mockApi({ "/health": { status: "ok", version: "0.1.0" }, "/sessions": [SUMMARY] });
    renderAt("/");
    expect(await screen.findByRole("link", { name: "Contrôle T2" })).toHaveAttribute("href", `/sessions/${SUMMARY.session_id}`);
    expect(screen.getByText("20 200")).toBeInTheDocument();
    expect(await screen.findByText("Moteur local connecté")).toBeInTheDocument();
  });

  it("affiche un état vide explicite", async () => {
    mockApi({ "/health": { status: "ok", version: "0.1.0" }, "/sessions": [] });
    renderAt("/");
    expect(await screen.findByText("Aucune session pour l'instant")).toBeInTheDocument();
  });

  it("affiche une erreur lisible quand le moteur est injoignable", { timeout: 10_000 }, async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("sidadr serve");
    // L'état « injoignable » n'apparaît qu'après trois nouvelles tentatives (une par seconde).
    expect(await screen.findByText("Moteur injoignable", {}, { timeout: 6_000 })).toBeInTheDocument();
  });
});

describe("vue d'ensemble", () => {
  it("propose de lancer l'analyse puis affiche le score", async () => {
    let analysed = false;
    const analysis = {
      generated_at: "2026-11-19T12:05:00+00:00",
      sidadr_version: "0.1.0",
      rows_analyzed: 20199,
      journal_entries_at_analysis: 6,
      score: {
        score: 99.4,
        notes: ["Indicateur interne paramétrable."],
        dimensions: [
          { name: "completeness", label: "Complétude", definition: "", weight: 0.3, effective_weight: 0.3, value: 0.999, numerator: 239, denominator: 201990, note: "" },
        ],
      },
      findings: { total: 1656, by_severity: { certain_error: 468, probable_inconsistency: 160, atypical_value: 122, review_alert: 906 }, by_kind: {}, by_status: {} },
      rules: [],
      rule_parameters: {},
    };
    const fetchMock = mockApi({
      "/health": { status: "ok", version: "0.1.0" },
      [`/sessions/${SUMMARY.session_id}`]: () => (analysed ? { ...STATE, analysis, analysis_up_to_date: true } : STATE),
      [`/sessions/${SUMMARY.session_id}/analyze`]: () => {
        analysed = true;
        return analysis;
      },
    });
    renderAt(`/sessions/${SUMMARY.session_id}`);

    expect(await screen.findByText("Cette session n'a pas encore été analysée")).toBeInTheDocument();
    expect(screen.getByText("Journal d'audit intact (chaîne d'empreintes vérifiée)")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Lancer l'analyse" })[0]!);

    expect(await screen.findByRole("img", { name: "Score qualité 99.4 sur 100" })).toBeInTheDocument();
    expect(screen.getByText("468")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Relancer l'analyse" })).toBeEnabled();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${SUMMARY.session_id}/analyze`, expect.objectContaining({ method: "POST" })),
    );
  });

  it("signale une analyse dépassée", async () => {
    mockApi({
      "/health": { status: "ok", version: "0.1.0" },
      [`/sessions/${SUMMARY.session_id}`]: {
        ...STATE,
        analysis_up_to_date: false,
        analysis: {
          generated_at: "2026-11-19T12:05:00+00:00",
          sidadr_version: "0.1.0",
          rows_analyzed: 10,
          journal_entries_at_analysis: 1,
          score: { score: 90, notes: [], dimensions: [] },
          findings: { total: 0, by_severity: {}, by_kind: {}, by_status: {} },
          rules: [],
          rule_parameters: {},
        },
      },
    });
    renderAt(`/sessions/${SUMMARY.session_id}`);
    expect(await screen.findByRole("status")).toHaveTextContent("relancez-la");
  });
});
