// Typed API functions, one per SPEC §11.2 endpoint the dashboard uses.
import { request, requestText } from "./client";
import type {
  ApiKeyCreated,
  ApiKeySummary,
  AuditCheckpoint,
  AuditEvent,
  AuditTimelineEvent,
  AuditVerification,
  DecisionSummary,
  DryRunResult,
  EvaluationDetail,
  EvaluationSummary,
  Listed,
  Me,
  Member,
  MemberCreated,
  Metrics,
  ModelCall,
  Paginated,
  Policy,
  PurgeResult,
  RenderedPrompt,
  ReviewCompleteBody,
  ReviewDetail,
  ReviewKind,
  ReviewSummary,
  Role,
  ValidateResult,
  VersionDetail,
  VersionSummary,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointCreated,
  WebhookEventType,
  WorkspaceSettings,
} from "./types";

// --- Auth ---
export const auth = {
  csrf: () => request<{ csrfToken: string }>("/auth/csrf"),
  me: () => request<Me>("/auth/me"),
  login: (username: string, password: string) =>
    request<Me>("/auth/login", { method: "POST", body: { username, password } }),
  logout: () => request<{ status: string }>("/auth/logout", { method: "POST" }),
  switchWorkspace: (workspace: string) =>
    request<{ active_workspace: { id: string; slug: string } }>("/auth/workspace", {
      method: "POST",
      body: { workspace },
    }),
  changePassword: (new_password: string) =>
    request<{ status: string }>("/auth/password", {
      method: "POST",
      body: { new_password },
    }),
};

// --- Workspace settings ---
export const workspace = {
  getSettings: () => request<WorkspaceSettings>("/workspace/settings"),
  patchSettings: (patch: Partial<WorkspaceSettings>) =>
    request<WorkspaceSettings>("/workspace/settings", { method: "PATCH", body: patch }),
};

// --- Members ---
export const members = {
  list: () => request<Listed<Member>>("/members"),
  create: (username: string, role: Role) =>
    request<MemberCreated>("/members", { method: "POST", body: { username, role } }),
  update: (id: string, role: Role) =>
    request<Member>(`/members/${id}`, { method: "PATCH", body: { role } }),
  remove: (id: string) => request<void>(`/members/${id}`, { method: "DELETE" }),
};

// --- API keys ---
export const apiKeys = {
  list: () => request<Listed<ApiKeySummary>>("/api-keys"),
  create: (body: { name: string; scopes: string[]; decision_ids?: string[] | null }) =>
    request<ApiKeyCreated>("/api-keys", { method: "POST", body }),
  revoke: (id: string) => request<ApiKeySummary>(`/api-keys/${id}/revoke`, { method: "POST" }),
  rotate: (id: string) => request<ApiKeyCreated>(`/api-keys/${id}/rotate`, { method: "POST" }),
};

// --- Decisions / versions / policies ---
export const decisions = {
  list: (cursor?: string) =>
    request<Paginated<DecisionSummary>>("/decisions", { query: { cursor } }),
  get: (key: string) =>
    request<DecisionSummary & { versions: VersionSummary[] }>(`/decisions/${key}`),
  create: (body: { key: string; name: string; description?: string }) =>
    request<DecisionSummary>("/decisions", { method: "POST", body }),
  legalHold: (key: string, legal_hold: boolean) =>
    request<{ key: string; legal_hold: boolean }>(`/decisions/${key}/legal-hold`, {
      method: "POST",
      body: { legal_hold },
    }),
  metrics: (key: string, window?: { from?: string; to?: string; version?: number }) =>
    request<Metrics>(`/decisions/${key}/metrics`, { query: { ...window } }),

  versions: (key: string) => request<Listed<VersionSummary>>(`/decisions/${key}/versions`),
  version: (key: string, n: number) =>
    request<VersionDetail>(`/decisions/${key}/versions/${n}`),
  publish: (key: string, n: number, carry_over_policy: boolean) =>
    request<VersionDetail>(`/decisions/${key}/versions/${n}/publish`, {
      method: "POST",
      body: { carry_over_policy },
    }),
  retire: (key: string, n: number) =>
    request<VersionDetail>(`/decisions/${key}/versions/${n}/retire`, { method: "POST" }),
  acknowledgeModel: (key: string, n: number) =>
    request<VersionDetail>(`/decisions/${key}/versions/${n}/acknowledge-model`, {
      method: "POST",
    }),
  renderPrompt: (key: string, n: number, input: unknown) =>
    request<RenderedPrompt>(`/decisions/${key}/versions/${n}/render-prompt`, {
      method: "POST",
      body: { input },
    }),
  dryRun: (key: string, n: number, body: { input: unknown; skip_model?: boolean }) =>
    request<DryRunResult>(`/decisions/${key}/versions/${n}/dry-run`, { method: "POST", body }),

  validate: (body: { source: string; format: "yaml" | "json" }) =>
    request<ValidateResult>("/decisions/validate", {
      method: "POST",
      rawBody: body.source,
      contentType: body.format === "yaml" ? "application/yaml" : "application/json",
    }),

  policies: (key: string, n: number) =>
    request<Listed<Policy>>(`/decisions/${key}/versions/${n}/policies`),
  activePolicy: (key: string, n: number) =>
    request<{ policy: Policy | null } | Policy>(`/decisions/${key}/versions/${n}/policy`),
  proposePolicy: (
    key: string,
    n: number,
    body: { thresholds: Record<string, number>; audit_sample_rate: number; note?: string },
  ) =>
    request<Policy>(`/decisions/${key}/versions/${n}/policies`, { method: "POST", body }),
};

export const policies = {
  approve: (id: string) => request<Policy>(`/policies/${id}/approve`, { method: "POST" }),
  reject: (id: string) => request<Policy>(`/policies/${id}/reject`, { method: "POST" }),
  deactivate: (id: string) => request<Policy>(`/policies/${id}/deactivate`, { method: "POST" }),
};

// --- Evaluations ---
export interface EvaluationFilters {
  decision?: string;
  status?: string;
  decided_by?: string;
  route_reason?: string;
  external_id?: string;
  created_after?: string;
  created_before?: string;
  cursor?: string;
}
export const evaluations = {
  list: (filters: EvaluationFilters = {}) =>
    request<Paginated<EvaluationSummary>>("/evaluations", { query: { ...filters } }),
  get: (id: string) => request<EvaluationDetail>(`/evaluations/${id}`),
  events: (id: string) => request<Listed<AuditTimelineEvent>>(`/evaluations/${id}/events`),
  modelCalls: (id: string) => request<Listed<ModelCall>>(`/evaluations/${id}/model-calls`),
  cancel: (id: string, reason: string) =>
    request<EvaluationDetail>(`/evaluations/${id}/cancel`, { method: "POST", body: { reason } }),
  legalHold: (id: string, legal_hold: boolean) =>
    request<{ id: string; legal_hold: boolean }>(`/evaluations/${id}/legal-hold`, {
      method: "POST",
      body: { legal_hold },
    }),
  purge: (id: string, body: { scope: "input" | "all"; reason: string; ticket_ref?: string }) =>
    request<PurgeResult>(`/evaluations/${id}/purge`, { method: "POST", body }),
};

// --- Reviews ---
export interface ReviewFilters {
  status?: string;
  kind?: ReviewKind;
  decision?: string;
  reason?: string;
  evaluation?: string;
  cursor?: string;
}
export const reviews = {
  list: (filters: ReviewFilters = {}) =>
    request<Paginated<ReviewSummary>>("/reviews", { query: { ...filters } }),
  get: (id: string) => request<ReviewDetail>(`/reviews/${id}`),
  next: (body: { decision?: string; kind?: ReviewKind } = {}) =>
    request<ReviewDetail | null>("/reviews/next", { method: "POST", body }),
  claim: (id: string) => request<ReviewDetail>(`/reviews/${id}/claim`, { method: "POST" }),
  heartbeat: (id: string) =>
    request<{ lease_expires_at: string }>(`/reviews/${id}/heartbeat`, { method: "POST" }),
  release: (id: string) => request<ReviewDetail>(`/reviews/${id}/release`, { method: "POST" }),
  complete: (id: string, body: ReviewCompleteBody) =>
    request<ReviewDetail>(`/reviews/${id}/complete`, { method: "POST", body }),
};

// --- Audit ---
export const audit = {
  events: (cursor?: string) =>
    request<Paginated<AuditEvent>>("/audit/events", { query: { cursor } }),
  checkpoints: () => request<Listed<AuditCheckpoint>>("/audit/checkpoints"),
  publicKey: () => request<{ key_id: string; public_key: string }>("/audit/public-key"),
  export: (from?: number, to?: number) =>
    requestText("/audit/export", { query: { from, to } }),
  runVerification: (mode: string) =>
    request<AuditVerification>("/audit/verifications", { method: "POST", body: { mode } }),
  verification: (id: string) => request<AuditVerification>(`/audit/verifications/${id}`),
  latestVerification: () =>
    request<{ verification: AuditVerification | null } | AuditVerification>(
      "/audit/verifications/latest",
    ),
};

// --- Webhooks ---
export const webhooks = {
  list: () => request<Paginated<WebhookEndpoint>>("/webhooks"),
  create: (body: { url: string; event_types: WebhookEventType[]; decision?: string | null }) =>
    request<WebhookEndpointCreated>("/webhooks", { method: "POST", body }),
  update: (id: string, patch: Partial<{ active: boolean; event_types: WebhookEventType[] }>) =>
    request<WebhookEndpoint>(`/webhooks/${id}`, { method: "PATCH", body: patch }),
  rotateSecret: (id: string) =>
    request<WebhookEndpointCreated>(`/webhooks/${id}/rotate-secret`, { method: "POST" }),
  deliveries: (id: string, cursor?: string) =>
    request<Paginated<WebhookDelivery>>(`/webhooks/${id}/deliveries`, { query: { cursor } }),
  retryDelivery: (id: string) =>
    request<WebhookDelivery>(`/webhooks/deliveries/${id}/retry`, { method: "POST" }),
};
