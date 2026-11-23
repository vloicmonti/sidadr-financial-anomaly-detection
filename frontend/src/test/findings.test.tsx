import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Finding, FindingDetail, SessionState } from "../api/types";
import { AppShell } from "../components/AppShell";
import { FindingsPage } from "../pages/FindingsPage";

const SESSION = {
  session_id: "S1",
  name: "Démo",
  imports: [{ import_id: "I1", source_file: "alpha.csv" }],
  working_rows: 3,
  analysis: { total: 2 },
  analysis_up_to_date: true,
  schema: [
    { name: "transaction_id", label: "Identifiant d'opération", type: "text", required: true, values: [] },
    { name: "amount", label: "Montant", type: "decimal", required: true, values: [] },
  ],
} as unknown as SessionState;

const FINDING: Finding = {
  finding_id: "F-0001",
  kind: "duplicate_key",
  severity: "certain_error",
  record_ids: ["R1", "R2"],
  rule_id: "DUP-KEY",
  explanation: "L'identifiant T-1 apparaît sur 2 enregistrements.",
  recommendation: "Conserver un seul enregistrement.",
  field: "transaction_id",
  observed_value: "T-1",
  threshold: null,
  score: null,
  key: "k1",
  status: "open",
};

const DETAIL: FindingDetail = {
  ...FINDING,
  records: [
    { record_id: "R1", transaction_id: "T-1", amount: "10.00", excluded: false },
    { record_id: "R2", transaction_id: "T-1", amount: "12.00", excluded: false },
  ],
};

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function mockApi(session: SessionState = SESSION) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      if (url === "/api/health") return json({ status: "ok", version: "0.1.0" });
      if (url === "/api/sessions/S1") return json(session);
      if (url.startsWith("/api/sessions/S1/findings?"))
        return json({
          total: 1,
          page: 1,
          page_size: 50,
          items: [FINDING],
          facets: { severity: { certain_error: 1 }, kind: { duplicate_key: 1 }, status: { open: 1 } },
        });
      if (url === "/api/sessions/S1/findings/F-0001/decision") return json({ target: "D1", decision: "confirmed" }, 201);
      if (url === "/api/sessions/S1/findings/F-0001") return json(DETAIL);
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
          { path: "/sessions/:sessionId/findings", element: <FindingsPage /> },
          { path: "/sessions/:sessionId/findings/:view", element: <FindingsPage /> },
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

afterEach(() => vi.unstubAllGlobals());

describe("écran des constats", () => {
  it("la vue Doublons filtre par types et la barre latérale y mène", async () => {
    const calls = mockApi();
    renderAt("/sessions/S1/findings/duplicates");
    expect(await screen.findByRole("heading", { name: "Doublons" })).toBeInTheDocument();
    expect(await screen.findByText(FINDING.explanation)).toBeInTheDocument();
    const request = calls.find((c) => c.url.includes("/findings?"));
    const params = new URL(request!.url, "http://x").searchParams;
    expect(params.get("kind")).toBe("exact_duplicate_row,duplicate_key,cross_file_duplicate,combination_match");
    expect(params.get("page_size")).toBe("50");
    expect(screen.getByRole("link", { name: "Anomalies" })).toHaveAttribute("href", "/sessions/S1/findings/anomalies");
    expect(screen.getByRole("button", { name: /Erreur certaine/ })).toHaveTextContent("1");
  });

  it("filtre par sévérité via l'URL", async () => {
    const calls = mockApi();
    const router = renderAt("/sessions/S1/findings");
    await userEvent.click(await screen.findByRole("button", { name: /Erreur certaine/ }));
    await waitFor(() => expect(router.state.location.search).toContain("severity=certain_error"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("severity=certain_error"))).toBe(true));
  });

  it("ouvre le détail, surligne les différences et enregistre une décision motivée", async () => {
    const calls = mockApi();
    renderAt("/sessions/S1/findings");
    await userEvent.click(await screen.findByRole("button", { name: FINDING.explanation }));

    expect(await screen.findByText("Conserver un seul enregistrement.")).toBeInTheDocument();
    const amount = screen.getByRole("columnheader", { name: "Montant" });
    expect(amount.className).toContain("bg-warning-bg");
    expect(screen.getByRole("columnheader", { name: "Identifiant d'opération" }).className).not.toContain("bg-warning-bg");

    const submit = screen.getByRole("button", { name: "Enregistrer la décision" });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: /Rejeter/ }));
    await userEvent.type(screen.getByLabelText("Motif"), "Deux opérations distinctes");
    await userEvent.click(submit);

    expect(await screen.findByRole("status")).toHaveTextContent("Décision D1 enregistrée");
    const post = calls.find((c) => c.url.endsWith("/decision"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({ decision: "rejected", reason: "Deux opérations distinctes" });
  });

  it("invite à lancer l'analyse quand il n'y en a pas", async () => {
    const calls = mockApi({ ...SESSION, analysis: null } as unknown as SessionState);
    renderAt("/sessions/S1/findings");
    expect(await screen.findByText("Aucune analyse pour cette session")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: /vue d'ensemble/i })).toHaveAttribute("href", "/sessions/S1");
    expect(calls.some((c) => c.url.includes("/findings?"))).toBe(false);
  });

  it("signale une analyse périmée", async () => {
    mockApi({ ...SESSION, analysis_up_to_date: false } as unknown as SessionState);
    renderAt("/sessions/S1/findings");
    expect(await screen.findByText(/les constats ne sont plus à jour/)).toBeInTheDocument();
  });
});
