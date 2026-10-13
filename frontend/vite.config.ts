import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Django serves the built app; assets live under /static/ (see FRONTEND_DIST_DIR).
// In development the Vite server proxies API/admin calls to Django, keeping the
// same origin so session cookies and CSRF work unchanged.
const django = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/" : "/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: django },
      "/admin": { target: django },
      "/static/admin": { target: django },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "node" },
}));
