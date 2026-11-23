import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";
import type { SessionState } from "../api/types";
import { Button, ErrorState } from "./ui";

export const MIN_REASON = 5;

/** Affichage d'une valeur de cellule : les vides sont explicites. */
export function CellValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="text-ink-3">—</span>;
  return <>{String(value)}</>;
}

/** Après une modification des données, les constats affichés ne reflètent plus la copie de travail. */
export function StaleAnalysisBanner({ session }: { session: SessionState | undefined }) {
  if (!session?.analysis || session.analysis_up_to_date) return null;
  return (
    <div role="status" className="mb-4 flex items-center gap-2 rounded-lg bg-warning-bg px-4 py-2.5 text-[13px] text-warning">
      <CircleAlert aria-hidden className="size-4 shrink-0" />
      <span>
        Des données ont été corrigées depuis l'analyse : les constats ne sont plus à jour.{" "}
        <Link to={`/sessions/${session.session_id}`} className="font-medium underline">
          Relancer l'analyse depuis la vue d'ensemble
        </Link>
      </span>
    </div>
  );
}

/** Invalide tout ce qu'une correction, une exclusion, une décision ou une annulation peut changer. */
export function useInvalidateSession(sessionId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      ["session", "records", "record", "history", "findings", "finding"].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, sessionId] }),
      ),
    );
}

/** Champ « motif » obligatoire, partagé par toutes les actions journalisées. */
export function ReasonField({ value, onChange, label = "Motif" }: { value: string; onChange: (value: string) => void; label?: string }) {
  const id = useId();
  const help = useId();
  return (
    <div className="grid gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {label}
      </label>
      <textarea
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={help}
        rows={2}
        className="rounded-md border border-line bg-surface px-3 py-2 text-[13px]"
      />
      <span id={help} className="text-xs text-ink-3">
        Obligatoire ({MIN_REASON} caractères au moins), inscrit au journal d'audit.
      </span>
    </div>
  );
}

/** Annulation journalisée d'une correction (« C12 ») ou d'une décision (« D3 »). */
export function UndoButton({ sessionId, target, label = "Annuler" }: { sessionId: string; target: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const invalidate = useInvalidateSession(sessionId);
  const undo = useMutation({
    mutationFn: () => api.undo(sessionId, target, reason.trim()),
    onSuccess: async () => {
      setOpen(false);
      setReason("");
      await invalidate();
    },
  });

  if (!open) {
    return (
      <Button variant="ghost" className="h-7 px-2 text-[13px]" icon={Undo2} onClick={() => setOpen(true)}>
        {label}
      </Button>
    );
  }
  return (
    <form
      aria-label={`${label} ${target}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim().length >= MIN_REASON) undo.mutate();
      }}
      className="grid min-w-64 gap-2 rounded-lg border border-line bg-app p-3 text-left"
    >
      <ReasonField value={reason} onChange={setReason} label={`Motif de l'annulation de ${target}`} />
      {undo.isError ? <ErrorState title="Annulation refusée" error={undo.error} /> : null}
      <div className="flex justify-end gap-2">
        <Button className="h-8" onClick={() => setOpen(false)}>
          Fermer
        </Button>
        <Button type="submit" variant="primary" className="h-8" disabled={reason.trim().length < MIN_REASON || undo.isPending}>
          {undo.isPending ? "Annulation…" : `${label} ${target}`}
        </Button>
      </div>
    </form>
  );
}
