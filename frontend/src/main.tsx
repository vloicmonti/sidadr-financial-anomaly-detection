import "./styles/main.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Link, RouterProvider, createBrowserRouter } from "react-router";

import { ApiError } from "./api/client";
import { AppShell } from "./components/AppShell";
import { EmptyState } from "./components/ui";
import { FindingsPage } from "./pages/FindingsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ImportPage } from "./pages/ImportPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RecordsPage } from "./pages/RecordsPage";
import { SessionsPage } from "./pages/SessionsPage";

function NotFound() {
  return (
    <EmptyState title="Page introuvable" action={<Link to="/" className="font-medium text-primary hover:underline">Retour aux sessions</Link>}>
      Cette adresse ne correspond à aucun écran de SIDADR.
    </EmptyState>
  );
}

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <SessionsPage /> },
      { path: "/import", element: <ImportPage /> },
      { path: "/sessions/:sessionId", element: <OverviewPage /> },
      { path: "/sessions/:sessionId/findings", element: <FindingsPage /> },
      { path: "/sessions/:sessionId/findings/:view", element: <FindingsPage /> },
      { path: "/sessions/:sessionId/records", element: <RecordsPage /> },
      { path: "/sessions/:sessionId/history", element: <HistoryPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Une erreur métier (404, 422…) ne se corrige pas en réessayant.
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("Élément #root introuvable dans index.html.");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
