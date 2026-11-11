import { render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { makeQueryClient } from "./app/queryClient";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

beforeEach(() => {
  // Unauthenticated: /auth/me returns 401.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse(401, { error: { code: "unauthenticated", message: "no session" } }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows the login screen when unauthenticated", async () => {
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument(),
  );
});
