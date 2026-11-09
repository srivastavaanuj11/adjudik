import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      json: async () => ({ status: "ok", checks: { database: "ok", redis: "ok" } }),
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders the app heading", () => {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("heading", { level: 1, name: "Adjudik" })).toBeInTheDocument();
});
