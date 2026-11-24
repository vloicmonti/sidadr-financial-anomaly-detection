import { OctagonX, RotateCw } from "lucide-react";
import { Link, isRouteErrorResponse, useRouteError } from "react-router";

import { Button, Card } from "./ui";

/** Écran affiché à la place d'une page qui a planté : lisible, sans trace technique en avant. */
export function RouteError() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);

  return (
    <Card>
      <div role="alert" className="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <div className="grid size-11 place-items-center rounded-full bg-danger-bg text-danger">
          <OctagonX aria-hidden className="size-5" />
        </div>
        <h1 className="text-base font-semibold">Cet écran a rencontré une erreur</h1>
        <p className="max-w-md text-[13px] text-ink-2">
          Vos données ne sont pas touchées. Si le moteur a été lancé avec une version précédente de SIDADR, arrêtez-le
          et relancez <code className="rounded bg-subtle px-1 font-mono text-xs">sidadr serve</code>, puis rechargez la
          page.
        </p>
        <div className="flex gap-2">
          <Button variant="primary" icon={RotateCw} onClick={() => window.location.reload()}>
            Recharger la page
          </Button>
          <Link to="/" className="inline-flex h-9 items-center rounded-md border border-line px-3.5 font-medium hover:bg-subtle">
            Retour aux sessions
          </Link>
        </div>
        <details className="mt-2 max-w-xl text-left text-xs text-ink-3">
          <summary className="cursor-pointer">Détail technique</summary>
          <p className="mt-1 font-mono break-all">{detail}</p>
        </details>
      </div>
    </Card>
  );
}
