import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuditEntry, RecordDetail, SessionState } from "../api/types";
import { AppShell } from "../components/AppShell";
import { HistoryPage } from "../pages/HistoryPage";
import { RecordsPage } from "../pages/RecordsPage";

const SESSION = {
  session_id: "S1",
  name: "Stock",
  imports: [{ import_id: "I1", source_file: "pieces.csv" }],
  working_rows: 2,
  analysis: { total: 1 },
  analysis_up_to_date: false,
  schema: [
    { name: "code", label: "Code", type: "text", required: true, values: [] },
    { name: "prix", label: "Prix", type: "decimal", required: false, values: [] },
  ],
} as unknown as SessionState;

const ROWS = [
  { record_id: "pieces:1", code: "A1", prix: "1.60", excluded: false, corrected_fields: ["prix"] },
  { record_id: "pieces:2", code: "A2", prix: null, excluded: false, corrected_fields: [] },
];

const DETAIL: RecordDetail = {
  record_id: "pieces:2",
  original: { record_id: "pieces:2", source_file: "pieces.csv", source_row: 3, code: "A2", prix: null },
  working: { record_id: "pieces:2", code: "A2", prix: null },
  excluded: false,
  corrections: [],
  findings: [
    {
      finding_id: "RUL-00001",
      kind: "rule_violation",
      severity: "certain_error",
      record_ids: ["pieces:2"],
      rule_id: "REQ:prix",
      explanation: "prix est vide.",
      recommendation: "Compléter.",
      field: "prix",
      observed_value: null,
      threshold: null,
      score: null,
      key: "k",
      status: "open",
    },
  ],
};

const ENTRIES: AuditEntry[] = [
  { entry_id: 1, ts_utc: "2026-11-23T10:00:00+00:00", actor: "vincent", action_type: "decide", target: "D1", record_id: null, field: null, old_value: null, new_value: "deferred", reason: "à revoir", finding_id: "RUL-00001" },
  { entry_id: 2, ts_utc: "2026-11-23T10:01:00+00:00", actor: "vincent", action_type: "decide", target: "D2", record_id: null, field: null, old_value: "deferred", new_value: "confirmed", reason: "vérifié", finding_id: "RUL-00001" },
  { entry_id: 3, ts_utc: "2026-11-23T10:02:00+00:00", actor: "vincent", action_type: "correct", target: "C1", record_id: "pieces:1", field: "prix", old_value: "1.50", new_value: "1.60", reason: "tarif 2026", finding_id: null },
  { entry_id: 4, ts_utc: "2026-11-23T10:03:00+00:00", actor: "vincent", action_type: "correct", target: "C2", record_id: "pieces:2", field: "code", old_value: "A2", new_value: "A2b", reason: "erreur", finding_id: null },
  { entry_id: 5, ts_utc: "2026-11-23T10:04:00+00:00", actor: "vincent", action_type: "undo", target: "C2", record_id: "pieces:2", field: "code", old_value: "A2b", new_value: "A2", reason: "retour arrière", finding_id: null },
];

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function mockApi(overrides: Record<string, Response> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      const path = decodeURIComponent(url.split("?")[0] ?? url);
      if (overrides[path]) return overrides[path].clone();
      if (path === "/api/health") return json({ status: "ok", version: "0.1.0" });
      if (path === "/api/sessions/S1") return json(SESSION);
      if (path === "/api/sessions/S1/records") return json({ total: 2, page: 1, page_size: 50, items: ROWS });
      if (path === "/api/sessions/S1/records/pieces:2") return json(DETAIL);
      if (path === "/api/sessions/S1/corrections") return json({ target: "C3", old_value: null, new_value: "0.90" }, 201);
      if (path === "/api/sessions/S1/exclusions") return json({ target: "C4" }, 201);
      if (path === "/api/sessions/S1/undo") return json({ target: "C1", status: "undone" });
      if (path === "/api/sessions/S1/history") return json({ entries: ENTRIES, integrity: { entries: 5, valid: true, first_broken_entry: null } });
      return json({ detail: "inconnu" }, 404);
    }),
  );
  return calls;
}

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: "/sessions/:sessionId", element: <p>Vue d'ensemble</p> },
          { path: "/sessions/:sessionId/records", element: <RecordsPage /> },
          { path: "/sessions/:sessionId/history", element: <HistoryPage /> },
          { path: "/sessions/:sessionId/findings", element: <p>Constats</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const body = (calls: Call[], suffix: string) => JSON.parse(String(calls.find((c) => c.url.endsWith(suffix))?.init?.body));

afterEach(() => vi.unstubAllGlobals());

describe("écran des corrections", () => {
  it("liste les enregistrements, marque les valeurs corrigées et signale l'analyse périmée", async () => {
    const calls = mockApi();
    const router = renderAt("/sessions/S1/records");
    expect(await screen.findByText("1 corrigé")).toBeInTheDocument();
    expect(screen.getByTitle("Valeur corrigée")).toHaveTextContent("1.60");
    expect(screen.getByText(/les constats ne sont plus à jour/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Historique" })).toHaveAttribute("href", "/sessions/S1/history");

    await userEvent.click(screen.getByRole("checkbox", { name: /enregistrements exclus/ }));
    await waitFor(() => expect(router.state.location.search).toContain("excluded=1"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("include_excluded=true"))).toBe(true));
  });

  it("corrige un champ avec un motif depuis la fiche", async () => {
    const calls = mockApi();
    renderAt("/sessions/S1/records?r=pieces:2");
    expect(await screen.findByText(/pieces\.csv · ligne 3/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /prix est vide/ })).toHaveAttribute("href", "/sessions/S1/findings?f=RUL-00001");

    await userEvent.click(screen.getByRole("button", { name: "Corriger Prix" }));
    const form = screen.getByRole("form", { name: "Corriger Prix" });
    const submit = within(form).getByRole("button", { name: "Enregistrer la correction" });
    await userEvent.type(within(form).getByLabelText(/Nouvelle valeur/), "0,90");
    expect(submit).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Motif"), "tarif fournisseur");
    await userEvent.click(submit);

    await waitFor(() => expect(screen.queryByRole("form", { name: "Corriger Prix" })).not.toBeInTheDocument());
    expect(body(calls, "/corrections")).toEqual({ record_id: "pieces:2", field: "prix", value: "0,90", reason: "tarif fournisseur" });
  });

  it("affiche le refus du moteur sans fermer le formulaire", async () => {
    mockApi({
      "/api/sessions/S1/corrections": new Response(JSON.stringify({ detail: "prix attend un nombre : 'beaucoup' refusé.", code: "invalid_action" }), { status: 422 }),
    });
    renderAt("/sessions/S1/records?r=pieces:2");
    await userEvent.click(await screen.findByRole("button", { name: "Corriger Prix" }));
    const form = screen.getByRole("form", { name: "Corriger Prix" });
    await userEvent.type(within(form).getByLabelText(/Nouvelle valeur/), "beaucoup");
    await userEvent.type(within(form).getByLabelText("Motif"), "essai de saisie");
    await userEvent.click(within(form).getByRole("button", { name: "Enregistrer la correction" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Correction refusée");
    expect(within(form).getByRole("alert")).toHaveTextContent("attend un nombre");
  });

  it("exclut un enregistrement avec un motif", async () => {
    const calls = mockApi();
    renderAt("/sessions/S1/records?r=pieces:2");
    await userEvent.click(await screen.findByRole("button", { name: "Exclure" }));
    const form = screen.getByRole("form", { name: "Exclure l'enregistrement" });
    await userEvent.type(within(form).getByLabelText("Motif de l'exclusion"), "ligne de test");
    await userEvent.click(within(form).getByRole("button", { name: "Exclure l'enregistrement" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/exclusions"))).toBe(true));
    expect(body(calls, "/exclusions")).toEqual({ record_id: "pieces:2", reason: "ligne de test" });
  });
});

describe("historique", () => {
  it("montre l'intégrité et ne propose d'annuler que ce qui est encore actif", async () => {
    const calls = mockApi();
    renderAt("/sessions/S1/history");
    expect(await screen.findByText(/Journal intègre : 5 entrée\(s\)/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    // du plus récent au plus ancien : annulation C2, C2, C1, D2, D1
    const hasUndo = rows.map((r) => within(r).queryByRole("button", { name: "Annuler" }) !== null);
    expect(hasUndo).toEqual([false, false, true, true, false]);
    expect(within(rows[2]!).getByText("Prix")).toBeInTheDocument();

    await userEvent.click(within(rows[2]!).getByRole("button", { name: "Annuler" }));
    await userEvent.type(screen.getByLabelText("Motif de l'annulation de C1"), "ancien tarif rétabli");
    await userEvent.click(screen.getByRole("button", { name: "Annuler C1" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/undo"))).toBe(true));
    expect(body(calls, "/undo")).toEqual({ target: "C1", reason: "ancien tarif rétabli" });
  });

  it("filtre par type d'action", async () => {
    mockApi();
    renderAt("/sessions/S1/history");
    await screen.findByText(/Journal intègre/);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filtrer par action" }), "decide");
    expect(screen.getByText("2 entrée(s)")).toBeInTheDocument();
  });
});
