// Typed fetch wrapper for the Adjudik API (SPEC §11.1).
//
// - JSON only; base path /api/v1.
// - Session cookie auth plus X-CSRFToken on unsafe methods; the CSRF token is
//   read from the `csrftoken` cookie (fetched once via GET /auth/csrf).
// - The error envelope { error: { code, message, details } } is parsed into an
//   ApiError so callers and React Query can branch on `code`.

import type { ApiErrorBody, ApiErrorDetail } from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];
  readonly retryAfter: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: ApiErrorDetail[] = [],
    retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const c = part.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return null;
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  /** Body already serialized (used for YAML draft upload). */
  rawBody?: string;
  contentType?: string;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = path.startsWith("/api") ? path : `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== null && v !== undefined && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

let csrfReady: Promise<void> | null = null;
async function ensureCsrf(): Promise<void> {
  if (readCookie("csrftoken")) return;
  if (!csrfReady) {
    csrfReady = fetch(`${API_BASE}/auth/csrf`, {
      credentials: "same-origin",
    }).then(() => undefined);
  }
  await csrfReady;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };

  if (UNSAFE.has(method)) {
    await ensureCsrf();
    const token = readCookie("csrftoken");
    if (token) headers["X-CSRFToken"] = token;
  }

  let body: BodyInit | undefined;
  if (opts.rawBody !== undefined) {
    body = opts.rawBody;
    headers["Content-Type"] = opts.contentType ?? "application/json";
  } else if (opts.body !== undefined) {
    body = JSON.stringify(opts.body);
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });

  if (resp.status === 204) return undefined as T;

  const text = await resp.text();
  const data = text ? safeJson(text) : null;

  if (!resp.ok) {
    const envelope = data as ApiErrorBody | null;
    const err = envelope?.error;
    const retryAfter = resp.headers.get("Retry-After");
    throw new ApiError(
      resp.status,
      err?.code ?? `http_${resp.status}`,
      err?.message ?? resp.statusText,
      err?.details ?? [],
      retryAfter ? Number(retryAfter) : null,
    );
  }

  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** Fetch raw text (NDJSON audit export). */
export async function requestText(path: string, opts: RequestOptions = {}): Promise<string> {
  const resp = await fetch(buildUrl(path, opts.query), {
    headers: { Accept: "*/*" },
    credentials: "same-origin",
    signal: opts.signal,
  });
  if (!resp.ok) {
    throw new ApiError(resp.status, `http_${resp.status}`, resp.statusText);
  }
  return resp.text();
}
