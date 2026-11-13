import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API is the backend on localhost:8000; in production the backend serves the built app itself, on the same origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/sessions": "http://127.0.0.1:8000", "/healthz": "http://127.0.0.1:8000" } },
  test: { environment: "node" },
});
