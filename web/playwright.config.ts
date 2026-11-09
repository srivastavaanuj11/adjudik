import { defineConfig, devices } from "@playwright/test";

// Playwright runs against the running compose stack (SPEC §16.1 `make e2e`).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: "html",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
