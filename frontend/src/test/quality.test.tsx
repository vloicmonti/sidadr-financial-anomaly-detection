import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalysisSummary, SessionState } from "../api/types";
import { AppShell } from "../components/AppShell";
import { ExportsPage } from "../pages/ExportsPage";
import { QualityPage } from "../pages/QualityPage";

const ANALYSIS = {
  generated_at: "2026-11-23T10:00:00+00:00",
  sidadr_version: "0.1.0",
  rows_analyzed: 4,
  score: {
    score: 87.5,
    notes: ["Score indicatif."],
    dimensions: [
      { name: "completeness", label: "Complétude", definition: "cellules obligatoires remplies", weight: 0.5, effective_weight: 0.5, value: 0.75, numerator: 1, denominator: 4, note: "" },
      { name: "consistency", label: "Cohérence", definition: "lignes sans violation", weight: 0.5, effective_weight: 0, value: null, numerator: 0, denominator: 0, note: "Règles métier non évaluées." },
    ],
  },
  findings: { total: 1, by_severity: {}, by_kind: {}, by_status: {} },
  rules: [{ rule_id: "R-01", description: "Le montant est positif", severity: "certain_error", dimension: "consistency", checked: 4, violations: 1, not_evaluable: 0 }],
  rule_parameters: {},
  profile: {
    rows: 4,
    duplicate_rows: 0,
    duplicate_groups: 0,
    columns: [
      {
        name: "prix", label: "Prix", type: "decimal", required: true, rows: 4, missing: 1, distinct: 3, invalid: 1,
        checked_for_validity: true, invalid_reasons: { "pas un nombre": 1 }, invalid_samples: ["pieces:3"], decisions: [],
        numeric: { count: 2, min: "0.10", q1: "0.10", median: "0.50", q3: "0.90", max: "0.90", mean: 0.5, std: 0.4 },
        date_range: null, top_values: [["0.90", 1]],
      },
      {
        name: "libelle", label: "Libellé", type: "text", required: false, rows: 4, missing: 0, distinct: 4, invalid: 0,
        checked_for_validity: false, invalid_reasons: {}, invalid_samples: [], decisions: [], numeric: null, date_range: null,
        top_values: [["Vis", 1]],
      },
    ],
  },
} as unknown as AnalysisSummary;

const SESSION = {
  session_id: "S1",
  name: "Stock",
  rows: 4,
  imports: [{ import_id: "I1", source_file: "pieces.csv" }],
  working_rows: 4,
  excluded_records: 0,
  active_corrections: 0,
  active_decisions: 0,
  journal_entries: 0,
  journal_integrity: "intact",
  analysis: ANALYSIS,
  analysis_up_to_date: true,
} as unknown as SessionState;

function mockApi(session: SessionState) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
      if (url === "/api/health") return json({ status: "ok", version: "0.1.0" });
      if (url === "/api/sessions/S1") return json(session);
      return new Response(JSON.stringify({ detail: "inconnu" }), { status: 404 });
    }),
  );
}

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: "/sessions/:sessionId", element: <p>Vue d'ensemble</p> },
          { path: "/sessions/:sessionId/quality", element: <QualityPage /> },
          { path: "/sessions/:sessionId/exports", element: <ExportsPage /> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("qualité des données", () => {
  it("détaille les dimensions, le profil des colonnes et les règles", async () => {
    mockApi(SESSION);
    renderAt("/sessions/S1/quality");
    const dimensions = await screen.findByRole("row", { name: /Complétude/ });
    expect(dimensions).toHaveTextContent("1 sur 4");
    expect(screen.getByRole("row", { name: /Cohérence/ })).toHaveTextContent("non évaluée");
    expect(screen.getByRole("row", { name: /Libellé/ })).toHaveTextContent("non contrôlée");
    expect(screen.getByRole("row", { name: /Le montant est positif/ })).toHaveTextContent("1");

    await userEvent.click(screen.getByRole("button", { name: "Détail de Prix" }));
    expect(screen.getByText("Médiane").nextSibling).toHaveTextContent("0,5");
    expect(screen.getByText("pas un nombre")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "pieces:3" })).toHaveAttribute("href", "/sessions/S1/records?r=pieces%3A3");
    expect(screen.getByRole("link", { name: "Qualité des données" })).toHaveAttribute("href", "/sessions/S1/quality");
  });

  it("explique quand le profil manque dans une ancienne analyse", async () => {
    mockApi({ ...SESSION, analysis: { ...ANALYSIS, profile: undefined } } as unknown as SessionState);
    renderAt("/sessions/S1/quality");
    expect(await screen.findByText("Profil par colonne indisponible")).toBeInTheDocument();
  });
});

describe("rapports et exports", () => {
  it("propose le rapport et les deux exports avec les bonnes adresses", async () => {
    mockApi(SESSION);
    renderAt("/sessions/S1/exports");
    expect(await screen.findByRole("link", { name: "Ouvrir" })).toHaveAttribute("href", "/api/sessions/S1/report");
    const downloads = screen.getAllByRole("link", { name: "Télécharger" }).map((a) => a.getAttribute("href"));
    expect(downloads).toEqual(["/api/sessions/S1/report", "/api/sessions/S1/export?format=xlsx", "/api/sessions/S1/export?format=csv"]);
    expect(screen.getByText("0 entrée(s), intact")).toBeInTheDocument();
  });

  it("désactive le rapport tant qu'aucune analyse n'existe", async () => {
    mockApi({ ...SESSION, analysis: null } as unknown as SessionState);
    renderAt("/sessions/S1/exports");
    expect(await screen.findByText("Disponible après une analyse")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Télécharger" })).toHaveLength(2);
    expect(within(screen.getByRole("main")).getByText(/ne contiendront aucun constat/)).toBeInTheDocument();
  });
});
