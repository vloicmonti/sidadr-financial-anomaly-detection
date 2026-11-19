import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ChevronRight,
  Copy,
  Database,
  Download,
  FolderOpen,
  Gauge,
  History,
  LayoutDashboard,
  ListChecks,
  Pencil,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import { Link, NavLink, Outlet, useParams } from "react-router";

import { api } from "../api/client";
import { cn, formatNumber } from "../lib";

interface NavItem {
  label: string;
  icon: LucideIcon;
  to?: (sessionId: string) => string; // absent : écran pas encore livré
}

const SESSION_NAV: { group: string; items: NavItem[] }[] = [
  { group: "Session", items: [{ label: "Vue d'ensemble", icon: LayoutDashboard, to: (id) => `/sessions/${id}` }] },
  { group: "Données", items: [{ label: "Qualité des données", icon: Gauge }] },
  {
    group: "Détection",
    items: [
      { label: "Doublons", icon: Copy },
      { label: "Noms & identités", icon: Users },
      { label: "Anomalies", icon: Activity },
      { label: "Règles métier", icon: ListChecks },
    ],
  },
  { group: "Traitement", items: [{ label: "Corrections", icon: Pencil }, { label: "Historique", icon: History }] },
  { group: "Sorties", items: [{ label: "Rapports & exports", icon: Download }] },
];

const ITEM = "flex items-center gap-2.5 rounded-md px-2.5 py-[7px] font-medium transition-colors";

function Sidebar() {
  const { sessionId } = useParams();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000, retry: false });

  return (
    <aside className="sticky top-0 flex h-screen flex-col border-r border-line bg-sidebar">
      <Link to="/" className="flex items-center gap-2.5 px-5 pt-4.5 pb-3.5">
        <span className="grid size-[30px] place-items-center rounded-lg bg-navy text-white">
          <ShieldCheck aria-hidden className="size-4" />
        </span>
        <span>
          <span className="block text-[15px] font-bold tracking-wide">SIDADR</span>
          <span className="block text-xs leading-tight text-ink-3">Financial Data Quality</span>
        </span>
      </Link>

      <nav aria-label="Navigation principale" className="flex-1 overflow-y-auto px-3 py-1">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            cn(ITEM, isActive ? "bg-brand-100 text-primary" : "text-ink-2 hover:bg-subtle hover:text-ink")
          }
        >
          <FolderOpen aria-hidden className="size-4" />
          Sessions
        </NavLink>

        {sessionId
          ? SESSION_NAV.map(({ group, items }) => (
              <div key={group}>
                <p className="px-2.5 pt-4.5 pb-1.5 text-xs font-medium text-ink-3">{group}</p>
                {items.map(({ label, icon: Icon, to }) =>
                  to ? (
                    <NavLink
                      key={label}
                      to={to(sessionId)}
                      end
                      className={({ isActive }) =>
                        cn(ITEM, isActive ? "bg-brand-100 text-primary" : "text-ink-2 hover:bg-subtle hover:text-ink")
                      }
                    >
                      <Icon aria-hidden className="size-4" />
                      {label}
                    </NavLink>
                  ) : (
                    <span
                      key={label}
                      aria-disabled="true"
                      title="Écran livré à l'étape suivante"
                      className={cn(ITEM, "cursor-default text-ink-3")}
                    >
                      <Icon aria-hidden className="size-4" />
                      {label}
                      <span className="ml-auto text-[11px] font-normal">bientôt</span>
                    </span>
                  ),
                )}
              </div>
            ))
          : null}
      </nav>

      <div className="m-3 grid gap-0.5 rounded-lg border border-line bg-surface p-3 text-xs text-ink-2">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
          <span
            aria-hidden
            className={cn("size-1.5 rounded-full", health.isSuccess ? "bg-success" : health.isError ? "bg-danger" : "bg-ink-3")}
          />
          {health.isSuccess ? "Moteur local connecté" : health.isError ? "Moteur injoignable" : "Connexion…"}
        </span>
        <span>{health.isSuccess ? `SIDADR ${health.data.version}` : "Lancez : sidadr serve"}</span>
      </div>
    </aside>
  );
}

function Topbar() {
  const { sessionId } = useParams();
  const session = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.session(sessionId ?? ""),
    enabled: Boolean(sessionId),
  });

  return (
    <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-line bg-app/85 px-8 backdrop-blur-sm backdrop-saturate-150">
      <nav aria-label="Fil d'Ariane" className="flex min-w-0 items-center gap-1.5 text-[13px] text-ink-3">
        <Link to="/" className="hover:text-ink">
          Sessions
        </Link>
        {sessionId ? (
          <>
            <ChevronRight aria-hidden className="size-3.5" />
            <span className="truncate font-medium text-ink">{session.data?.name ?? sessionId}</span>
          </>
        ) : null}
      </nav>
      {session.data ? (
        <span className="ml-auto inline-flex h-[30px] items-center gap-2 rounded-full border border-line bg-surface px-2.5 text-[13px] text-ink-2">
          <Database aria-hidden className="size-3.5" />
          <b className="font-medium text-ink">{session.data.imports.length} fichiers</b>·{" "}
          {formatNumber(session.data.working_rows)} enregistrements de travail
        </span>
      ) : null}
    </header>
  );
}

export function AppShell() {
  return (
    <div className="grid min-h-screen grid-cols-[248px_1fr]">
      <Sidebar />
      <div className="min-w-0">
        <Topbar />
        <main className="max-w-[1400px] px-8 pt-7 pb-12">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
