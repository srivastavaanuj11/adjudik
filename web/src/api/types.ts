// Typed contract for the Adjudik API (SPEC §11).
//
// These interfaces mirror the server's documented JSON response shapes. The
// server's endpoints are hand-rolled DRF APIViews without serializers, so the
// generated OpenAPI document (schemas/openapi.json) carries the path/operation
// inventory but no typed response components; this module is the typed layer the
// dashboard builds on (ADR 0007). The Playwright flows (§17) exercise these
// shapes against the real API.

export type Role = "admin" | "author" | "reviewer" | "auditor";

export type ApiKeyScope = "evaluate" | "read" | "read_sensitive" | "manage";

export type EvaluationStatus =
  | "processing"
  | "pending_review"
  | "decided"
  | "cancelled";

export type DecidedBy = "rule" | "model" | "human" | null;

export type ReviewKind = "gate" | "audit";

export type ReviewStatus = "open" | "claimed" | "completed" | "cancelled";

export type VersionStatus = "draft" | "active" | "retired";

export type PolicyStatus =
  | "proposed"
  | "active"
  | "rejected"
  | "deactivated"
  | "superseded";

export type PolicySource = "manual" | "calibration" | "carried_over";

export type ClaimMode = "any" | "next_only";

/** The error envelope (§11.1). */
export interface ApiErrorDetail {
  path?: string;
  code: string;
  message: string;
}
export interface ApiErrorBody {
  error: { code: string; message: string; details?: ApiErrorDetail[] };
}

export interface Paginated<T> {
  results: T[];
  next_cursor: string | null;
}

export interface Listed<T> {
  results: T[];
}

// --- Auth ---

export interface WorkspaceRef {
  id: string;
  slug: string;
  name?: string;
}
export interface Membership {
  workspace: { id: string; slug: string };
  role: Role;
}
export interface Me {
  username: string;
  active_workspace: WorkspaceRef | null;
  role: Role | null;
  memberships: Membership[];
}

// --- Workspace settings (§8.1) ---

export interface WorkspaceSettings {
  min_threshold: number;
  min_audit_sample_rate: number;
  calibration_blind_rate: number;
  require_four_eyes: boolean;
  reviewer_claim_mode: ClaimMode;
  daily_token_budget: number | null;
  breaker_window: number | null;
  breaker_min_audits: number | null;
}

// --- Members / API keys ---

export interface Member {
  id: string;
  username: string;
  role: Role;
}
export interface MemberCreated extends Member {
  temporary_password?: string;
}

export interface ApiKeySummary {
  id: string;
  name: string;
  prefix: string;
  scopes: ApiKeyScope[];
  decision_ids: string[] | null;
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}
export interface ApiKeyCreated extends ApiKeySummary {
  key: string; // returned once
}

// --- Decisions / versions / policies ---

export interface DecisionSummary {
  id: string;
  key: string;
  name: string;
  description: string;
  legal_hold: boolean;
  created_at: string;
}

export interface Outcome {
  id: string;
  label: string;
}

export interface VersionSummary {
  version: number;
  status: VersionStatus;
  definition_hash: string;
  rules_hash: string | null;
  model_step_fingerprint: string;
  created_at: string;
  published_at: string | null;
  retired_at: string | null;
}
export interface VersionDetail extends VersionSummary {
  decision: string;
  definition: DecisionDefinition;
  source_text: string;
  source_format: "yaml" | "json";
  observed_model: string | null;
  created_by: string | null;
  updated_by: string | null;
  published_by: string | null;
  updated_at: string;
}

/** Just the parts of the compiled definition the dashboard reads. */
export interface DecisionDefinition {
  key: string;
  name: string;
  description?: string | null;
  outcomes: Outcome[];
  rules?: {
    inputs?: { id: string; expr: string }[];
    rows?: RuleRow[];
    hit_policy?: string;
  } | null;
  model_step?: {
    provider: string;
    model: string;
    fields?: string[];
    policy_text?: string | null;
  } | null;
  review?: {
    reason_codes?: { id: string; label: string }[];
    sla_minutes?: number;
  } | null;
  input_schema?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RuleRow {
  id: string;
  when?: Record<string, string>;
  outcome?: string | null;
  notes?: string | null;
  [k: string]: unknown;
}

export interface Policy {
  id: string;
  decision_version_id: string;
  status: PolicyStatus;
  thresholds: Record<string, number>;
  targets: Record<string, number>;
  audit_sample_rate: number;
  calibration_map: unknown | null;
  source: PolicySource;
  note: string | null;
  proposed_by: string | null;
  proposed_at: string;
  decided_by_user: string | null;
  decided_at: string | null;
  activated_at: string | null;
  deactivated_at: string | null;
  deactivation_reason: string | null;
}

export interface ValidationIssue {
  path?: string;
  code: string;
  message: string;
  severity?: string;
}
export interface ValidateResult {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface RuleResult {
  type: string;
  matched_row_ids: string[];
  row_id: string | null;
  outcome: string | null;
}
export interface DryRunModel {
  ok: boolean;
  suggested_outcome: string | null;
  raw_confidence: number | null;
  rationale: string | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
}
export interface DryRunResult {
  rule_result: RuleResult;
  rule_trace: unknown;
  screen_flags: string[];
  model: DryRunModel | null;
  warnings: string[];
  status: "decided" | "needs_review";
  outcome: string | null;
  decided_by: DecidedBy;
  route_reason: string | null;
}

export interface RenderedPrompt {
  system: string;
  user: string;
}

// --- Evaluations ---

export interface EvaluationModel {
  suggested_outcome: string | null;
  raw_confidence: number | null;
  calibrated_confidence: number | null;
  threshold: number | null;
}
export interface EvaluationReview {
  id: string;
  kind: ReviewKind;
  status: ReviewStatus;
  due_at: string;
}
export interface EvaluationSummary {
  id: string;
  decision: string;
  version: number | null;
  external_id: string | null;
  status: EvaluationStatus;
  state_version: number;
  outcome: string | null;
  decided_by: DecidedBy;
  route_reason: string | null;
  rule_result: RuleResult | null;
  model: EvaluationModel | null;
  review: EvaluationReview | null;
  created_at: string;
  decided_at: string | null;
  metadata: Record<string, unknown>;
}
export interface EvaluationDetail extends EvaluationSummary {
  // sensitive fields, present only with read_sensitive
  input?: Record<string, unknown> | null;
  minimized_input?: Record<string, unknown> | null;
  rule_trace?: unknown;
  screen_flags?: string[];
  model_rationale?: string | null;
  legal_hold?: boolean;
  cancelled_at?: string | null;
  cancel_reason?: string | null;
}

export interface AuditTimelineEvent {
  seq: number;
  event_type: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  subject_type: string | null;
  subject_id: string | null;
  payload: Record<string, unknown>;
}

export interface ModelCall {
  id: string;
  status: string;
  superseded: boolean;
  purpose: string;
  sample_index: number | null;
  attempt: number;
  provider: string;
  model_requested: string;
  model_reported: string | null;
  valid: boolean | null;
  error_codes: string[];
  stop_reason: string | null;
  provider_error_code: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface PurgeResult {
  id: string;
  input_purged_at: string | null;
  explanation_purged_at: string | null;
}

// --- Reviews ---

export interface ReviewSummary {
  id: string;
  kind: ReviewKind;
  reason: string;
  status: ReviewStatus;
  blind: boolean;
  priority: number;
  decision: string;
  external_id: string | null;
  due_at: string;
  lease_expires_at: string | null;
  created_at: string;
  suggested_outcome?: string;
}

export interface ReviewDecisionBlock {
  key: string;
  name: string;
  version: number;
  outcomes: Outcome[];
  reason_codes: { id: string; label: string }[];
}
export interface ReviewSuggestion {
  outcome: string | null;
  rationale: string | null;
}
export interface ReviewPriorEvaluation {
  id: string;
  created_at: string;
  status: EvaluationStatus;
}
export interface ReviewDetail {
  id: string;
  kind: ReviewKind;
  reason: string;
  status: ReviewStatus;
  blind: boolean;
  lease_expires_at: string | null;
  due_at: string;
  decision: ReviewDecisionBlock;
  case: {
    input: Record<string, unknown>;
    included_paths: string[];
    external_id: string | null;
  };
  rules: {
    inputs: { id: string; expr: string }[];
    rows: string[];
    trace: unknown;
  };
  suggestion: ReviewSuggestion | null;
  prior_evaluations: ReviewPriorEvaluation[];
}

export interface ReviewCompleteBody {
  action: "accept" | "override" | "decide";
  outcome: string;
  reason_codes?: string[];
  reason_text?: string | null;
  notes?: string | null;
}

// --- Metrics (§11.7) ---

export interface AgreementInterval {
  point: number | null;
  lower: number | null;
  upper: number | null;
  n: number;
  effective_n: number;
}
export interface Percentiles {
  p50: number | null;
  p95: number | null;
}
export interface MetricsDaily {
  date: string;
  volume: number;
  decided_by: { rule: number; model: number; human: number };
  turnaround_p50_min: number | null;
}
export interface Metrics {
  window: { from: string; to: string };
  version: number | null;
  volume: number;
  decided_by: { rule: number; model: number; human: number };
  automation_rate: number;
  model_automation_rate: number;
  review_rate: number;
  override_rate: number;
  audit_agreement: AgreementInterval;
  audit_completion_rate: {
    rate: number;
    completed: number;
    created: number;
    open_backlog: number;
  };
  queue: { open: number; claimed: number; overdue: number };
  latency_ms: { rule: Percentiles; model: Percentiles };
  review_turnaround_min: Percentiles;
  route_reasons: Record<string, number>;
  tokens: { input: number; output: number };
  cost_usd: number;
  daily: MetricsDaily[];
}

// --- Audit (§9.5/§9.6) ---

export interface AuditEvent extends AuditTimelineEvent {
  evaluation_id: string | null;
  prev_hash: string;
  hash: string;
}
export interface AuditCheckpoint {
  seq: number;
  hash: string;
  created_at: string;
  key_id: string;
  signature: string;
  anchored_at: string | null;
}
export interface AuditVerification {
  id: string;
  mode: string;
  status: string;
  ok: boolean | null;
  from_seq: number | null;
  head_seq: number | null;
  first_bad_seq: number | null;
  mismatches: unknown;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

// --- Webhooks (§11.6) ---

export type WebhookEventType =
  | "evaluation.decided"
  | "evaluation.pending_review"
  | "evaluation.cancelled"
  | "audit.disagreement"
  | "automation.suspended";

export interface WebhookEndpoint {
  id: string;
  url: string;
  decision: string | null;
  event_types: WebhookEventType[];
  active: boolean;
  created_at: string;
}
export interface WebhookEndpointCreated extends WebhookEndpoint {
  secret: string; // returned once
}
export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_id: string;
  event_type: string;
  evaluation_id: string | null;
  status: string;
  attempt: number;
  last_status_code: number | null;
  last_error_class: string | null;
  next_attempt_at: string | null;
  created_at: string;
  delivered_at: string | null;
}
