import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../components/AppShell";
import { RouteError } from "../components/RouteError";

function Broken(): never {
  throw new TypeError("Cannot read properties of undefined (reading 'length')");
}

afterEach(() => vi.restoreAllMocks());

describe("page d'erreur", () => {
  it("remplace un écran qui plante par un message lisible, barre latérale conservée", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined); // React journalise l'erreur attendue
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ status: "ok", version: "0.1.0" }))));
    const router = createMemoryRouter(
      [{ element: <AppShell />, children: [{ errorElement: <RouteError />, children: [{ path: "/", element: <Broken /> }] }] }],
      { initialEntries: ["/"] },
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Cet écran a rencontré une erreur");
    expect(screen.getByRole("button", { name: "Recharger la page" })).toBeInTheDocument();
    expect(screen.getByText(/reading 'length'/)).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Navigation principale" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
