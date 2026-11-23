import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

declare const process: { env: Record<string, string | undefined> };

// En développement, l'API locale (sidadr serve, port 8000) est servie sous /api :
// le navigateur ne parle qu'à une seule origine. SIDADR_API permet de viser une autre instance.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": process.env.SIDADR_API ?? "http://127.0.0.1:8000" },
    fs: { allow: [".."] }, // les tokens du design system sont dans docs/design
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
