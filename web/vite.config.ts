// Vite config: in `npm run dev`, proxy the core's API and WebSockets (openfactory run, port 8000)
// so the dev server behaves like the built viewer served by the core.
import { defineConfig } from "vite";

const core = process.env.OPENFACTORY_CORE ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    proxy: {
      "/api": core,
      "/ws": { target: core.replace(/^http/, "ws"), ws: true },
    },
  },
});
