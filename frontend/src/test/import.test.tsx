import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SchemaField, UploadResult } from "../api/types";
import { AppShell } from "../components/AppShell";
import { ImportPage } from "../pages/ImportPage";

const SCHEMA: SchemaField[] = [
  { name: "transaction_id", label: "Identifiant d'opération", type: "text", required: true, values: [] },
  { name: "amount", label: "Montant", type: "decimal", required: true, values: [] },
];

const GENERIC: UploadResult = {
  upload_id: "a".repeat(32),
  suggested_mode: "generic",
  files: [
    {
      file: "factures.csv",
      kind: "csv",
      encoding: "utf-8-sig",
      delimiter: ";",
      sheet: null,
      rows: 402,
      rejected_rows: [{ row: 12, reason: "3 valeurs pour 4 colonnes attendues" }],
      rejected_count: 1,
      empty_columns: ["Commentaire"],
      warnings: [],
      headers: ["N° facture", "Fournisseur", "Montant HT"],
      preview: [["F-001", "ACME", "1 250,50"]],
      proposals: [],
      missing_required: ["transaction_id", "amount"],
    },
  ],
  generic_fields: [
    { name: "n_facture", label: "N° facture", type: "text", filled: 402, conformity: 1, sources: ["factures.csv"], reason: "texte" },
    { name: "fournisseur", label: "Fournisseur", type: "text", filled: 401, conformity: 1, sources: ["factures.csv"], reason: "texte" },
    { name: "montant_ht", label: "Montant HT", type: "decimal", filled: 402, conformity: 1, sources: ["factures.csv"], reason: "nombre (100% des valeurs)" },
  ],
};

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function mockApi(upload: UploadResult | Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      if (url === "/api/health") return json({ status: "ok", version: "0.1.0" });
      if (url === "/api/schema") return json(SCHEMA);
      if (url === "/api/imports") return upload instanceof Response ? upload : json(upload);
      if (url === "/api/sessions" && init?.method === "POST") return json({ session_id: "S-NEW", name: "x", imports: [] }, 201);
      if (url === "/api/sessions") return json([]);
      return json({ detail: "inconnu" }, 404);
    }),
  );
  return calls;
}

function renderImport() {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: "/import", element: <ImportPage /> },
          { path: "/sessions/:sessionId", element: <p>Session ouverte</p> },
        ],
      },
    ],
    { initialEntries: ["/import"] },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function csv(name: string, size = 20): File {
  return new File(["a;b\n".padEnd(size, "1")], name, { type: "text/csv" });
}

afterEach(() => vi.unstubAllGlobals());

describe("écran d'import", () => {
  it("refuse les formats non acceptés et liste les fichiers retenus", async () => {
    mockApi(GENERIC);
    renderImport();
    const input = screen.getByLabelText(/Glissez vos fichiers/i);
    await userEvent.upload(input, [csv("factures.csv"), new File(["x"], "image.png", { type: "image/png" })], { applyAccept: false });
    expect(screen.getByRole("alert")).toHaveTextContent("image.png : format non accepté");
    expect(screen.getByRole("list", { name: "Fichiers sélectionnés" })).toHaveTextContent("factures.csv");
    await userEvent.click(screen.getByRole("button", { name: "Retirer factures.csv" }));
    expect(screen.getByRole("button", { name: /Analyser/ })).toBeDisabled();
  });

  it("parcours générique : aperçu, clé, champ obligatoire, création", async () => {
    const calls = mockApi(GENERIC);
    const router = renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("factures.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));

    expect(await screen.findByText("Schéma déduit des fichiers")).toBeInTheDocument();
    expect(screen.getByText(/1 ligne\(s\) mal formée\(s\)/)).toBeInTheDocument();
    expect(screen.getByText(/Colonnes entièrement vides : Commentaire/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Aperçu/ })).toHaveTextContent("1 250,50");
    expect(screen.getByRole("radio", { name: /Opérations bancaires/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Générique/ })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "Montant HT : grouper les valeurs numériques" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "N° facture : clé d'unicité" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Fournisseur : obligatoire" }));
    await userEvent.click(screen.getByRole("button", { name: /Créer la session/ }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/sessions/S-NEW"));
    const created = calls.find((c) => c.url === "/api/sessions" && c.init?.method === "POST");
    expect(JSON.parse(String(created?.init?.body))).toEqual({
      upload_id: GENERIC.upload_id,
      mode: "generic",
      name: "factures.csv",
      key_fields: ["n_facture"],
      required_fields: ["fournisseur"],
      group_by: [],
    });
    const upload = calls.find((c) => c.url === "/api/imports");
    expect(upload?.init?.body).toBeInstanceOf(FormData);
  });

  it("permet de corriger le type détecté et l'envoie à la création", async () => {
    const calls = mockApi(GENERIC);
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("factures.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));
    const montant = await screen.findByRole("combobox", { name: "Montant HT : type" });
    expect(montant).toHaveValue("decimal");

    // Un nombre ne sert pas de groupe ; repassé en texte, il le peut.
    await userEvent.selectOptions(montant, "text");
    expect(screen.getByText("détecté : nombre")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Montant HT : grouper les valeurs numériques" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "N° facture : type" }), "decimal");
    expect(screen.queryByRole("checkbox", { name: "N° facture : grouper les valeurs numériques" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Créer la session/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions" && c.init?.method === "POST")).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.url === "/api/sessions" && c.init?.method === "POST")?.init?.body));
    expect(body.types).toEqual({ montant_ht: "text", n_facture: "decimal" });
  });

  it("déclare un contrôle de calcul entre colonnes de type nombre", async () => {
    const withNumbers: UploadResult = {
      ...GENERIC,
      generic_fields: [
        ...GENERIC.generic_fields,
        { name: "tva", label: "TVA", type: "decimal", filled: 402, conformity: 1, sources: ["factures.csv"], reason: "nombre" },
        { name: "montant_ttc", label: "Montant TTC", type: "decimal", filled: 402, conformity: 1, sources: ["factures.csv"], reason: "nombre" },
      ],
    };
    const calls = mockApi(withNumbers);
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("factures.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));
    await userEvent.click(await screen.findByRole("button", { name: "Ajouter un contrôle" }));

    const result = screen.getByRole("combobox", { name: "Contrôle 1 : résultat" });
    expect([...result.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Montant HT", "TVA", "Montant TTC"]);
    await userEvent.selectOptions(result, "montant_ttc");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Contrôle 1 : première colonne" }), "montant_ht");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Contrôle 1 : opération" }), "+");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Contrôle 1 : seconde colonne" }), "tva");
    const tolerance = screen.getByRole("textbox", { name: "Contrôle 1 : tolérance" });
    await userEvent.clear(tolerance);
    await userEvent.type(tolerance, "0,05");

    await userEvent.click(screen.getByRole("button", { name: /Créer la session/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions" && c.init?.method === "POST")).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.url === "/api/sessions" && c.init?.method === "POST")?.init?.body));
    expect(body.checks).toEqual([{ result: "montant_ttc", left: "montant_ht", operator: "+", right: "tva", tolerance: "0.05" }]);
  });

  it("retire un contrôle de calcul si une de ses colonnes n'est plus un nombre", async () => {
    mockApi({
      ...GENERIC,
      generic_fields: [
        ...GENERIC.generic_fields,
        { name: "tva", label: "TVA", type: "decimal", filled: 402, conformity: 1, sources: ["factures.csv"], reason: "nombre" },
      ],
    });
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("factures.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));
    await userEvent.click(await screen.findByRole("button", { name: "Ajouter un contrôle" }));
    expect(screen.getByRole("list", { name: "Contrôles de calcul" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "TVA : type" }), "text");
    expect(screen.queryByRole("list", { name: "Contrôles de calcul" })).not.toBeInTheDocument();
    expect(screen.getByText(/au moins deux colonnes de type nombre/)).toBeInTheDocument();
  });

  it("accepte les classeurs .xls et .ods", async () => {
    mockApi(GENERIC);
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), [
      new File(["x"], "ancien.xls", { type: "application/vnd.ms-excel" }),
      new File(["x"], "libre.ods", { type: "application/vnd.oasis.opendocument.spreadsheet" }),
    ]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Fichiers sélectionnés" })).toHaveTextContent(/ancien\.xls.*libre\.ods/);
  });

  it("parcours bancaire : correspondance modifiable et champs obligatoires contrôlés", async () => {
    const banking: UploadResult = {
      ...GENERIC,
      suggested_mode: "banking",
      files: [
        {
          ...GENERIC.files[0]!,
          file: "alpha.csv",
          missing_required: [],
          headers: ["Ref", "Valeur"],
          proposals: [
            { source: "Ref", target: "transaction_id", confidence: 1, method: "synonym", note: "" },
            { source: "Valeur", target: "amount", confidence: 0.82, method: "similarity", note: "nom proche : à vérifier" },
          ],
        },
      ],
    };
    const calls = mockApi(banking);
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("alpha.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));

    expect(await screen.findByText("Correspondance des colonnes")).toBeInTheDocument();
    expect(screen.getByText(/à vérifier \(82 %\)/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Champ associé à Valeur" }), "");
    expect(screen.getByRole("alert")).toHaveTextContent("alpha.csv : amount");
    expect(screen.getByRole("button", { name: /Créer la session/ })).toBeDisabled();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Champ associé à Valeur" }), "amount");
    await userEvent.click(screen.getByRole("button", { name: /Créer la session/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions" && c.init?.method === "POST")).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.init?.method === "POST" && c.url === "/api/sessions")?.init?.body));
    expect(body.mode).toBe("banking");
    expect(body.mappings).toEqual({ "alpha.csv": { Valeur: "amount" } });
  });

  it("affiche le refus du moteur lisiblement", async () => {
    mockApi(new Response(JSON.stringify({ detail: "x.csv semble binaire : ce n'est pas un fichier CSV texte.", code: "binary_csv" }), { status: 422 }));
    renderImport();
    await userEvent.upload(screen.getByLabelText(/Glissez vos fichiers/i), csv("x.csv"));
    await userEvent.click(screen.getByRole("button", { name: "Analyser le fichier" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("semble binaire");
  });
});
