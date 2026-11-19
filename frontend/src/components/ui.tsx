import type { ButtonHTMLAttributes, ReactNode } from "react";
import { AlertTriangle, Info, OctagonX, Activity, UserSearch, type LucideIcon } from "lucide-react";

import type { Severity } from "../api/types";
import { cn } from "../lib";

// --------------------------------------------------------------------------- bouton

type ButtonVariant = "primary" | "secondary" | "ghost";

const BUTTON: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-white shadow-[0_1px_2px_rgba(31,78,140,.25),inset_0_1px_0_rgba(255,255,255,.12)] hover:bg-primary-hover disabled:bg-subtle disabled:text-ink-3 disabled:shadow-none",
  secondary: "border border-line bg-surface text-ink shadow-sm hover:bg-subtle disabled:text-ink-3",
  ghost: "text-primary hover:bg-brand-50 disabled:text-ink-3",
};

export function Button({
  variant = "secondary",
  icon: Icon,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; icon?: LucideIcon | undefined }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-9 cursor-pointer items-center gap-2 rounded-md px-3.5 font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed",
        BUTTON[variant],
        className,
      )}
      {...props}
    >
      {Icon ? <Icon aria-hidden className="size-4" /> : null}
      {children}
    </button>
  );
}

// --------------------------------------------------------------------------- carte

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cn("min-w-0 rounded-lg border border-line bg-surface shadow-sm", className)}>
      {children}
    </section>
  );
}

export function CardHeader({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <header className="flex items-center gap-3 px-5.5 pt-4.5">
      <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
      {hint ? <span className="text-[13px] text-ink-3">{hint}</span> : null}
      {action ? <div className="ml-auto">{action}</div> : null}
    </header>
  );
}

// --------------------------------------------------------------------------- badges de sévérité

export const SEVERITIES: Record<Severity, { label: string; icon: LucideIcon; tone: string }> = {
  certain_error: { label: "Erreur certaine", icon: OctagonX, tone: "bg-danger-bg text-danger" },
  probable_inconsistency: { label: "Incohérence probable", icon: AlertTriangle, tone: "bg-warning-bg text-warning" },
  atypical_value: { label: "Valeur atypique", icon: Activity, tone: "bg-info-bg text-info" },
  review_alert: { label: "Alerte à examiner", icon: UserSearch, tone: "bg-review-bg text-review" },
};

export function SeverityBadge({ severity, short = false }: { severity: Severity; short?: boolean }) {
  const { label, icon: Icon, tone } = SEVERITIES[severity];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full py-px pr-2.5 pl-2 text-xs leading-5 font-medium whitespace-nowrap", tone)}>
      <Icon aria-hidden className="size-3.5" />
      {short ? label.split(" ")[0] : label}
    </span>
  );
}

// --------------------------------------------------------------------------- états

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-subtle", className)} />;
}

export function EmptyState({
  icon: Icon = Info,
  title,
  children,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <div className="grid size-11 place-items-center rounded-full bg-brand-50 text-primary">
        <Icon aria-hidden className="size-5" />
      </div>
      <h2 className="text-base font-semibold">{title}</h2>
      {children ? <div className="max-w-md text-[13px] text-ink-2">{children}</div> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Une erreur inattendue est survenue.";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger-line bg-danger-bg px-4 py-3.5 text-[13px] text-danger">
      <OctagonX aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <p className="font-semibold">Le chargement a échoué</p>
        <p className="mt-0.5">{message}</p>
      </div>
      {onRetry ? (
        <Button variant="secondary" className="h-8" onClick={onRetry}>
          Réessayer
        </Button>
      ) : null}
    </div>
  );
}
