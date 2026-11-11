import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev server on 5173, proxying API and health paths to the Django server (SPEC §16.2).
// In the compose network the server is reachable at http://server:8000.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig(({ command }) => ({
  // In production Django serves the SPA; built assets live under /static/ and are
  // served by WhiteNoise (SPEC §16.2). The dev server stays at the root.
  base: command === "build" ? "/static/" : "/",
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/healthz": { target: API_TARGET, changeOrigin: true },
      "/readyz": { target: API_TARGET, changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**"],
  },
}));
