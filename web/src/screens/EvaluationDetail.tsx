import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { evaluations } from "../api/endpoints";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { QueryBoundary, Loading, ErrorState } from "../ui/states";
import { Badge, JsonView, StatusBadge } from "../ui/components";
import { fmtConfidence, fmtDateTime, fmtNumber, titleCase } from "../ui/format";
import type { EvaluationDetail as Eval } from "../api/types";

export function EvaluationDetail() {
  const { id = "" } = useParams();
  const query = useQuery({ queryKey: ["evaluation", id], queryFn: () => evaluations.get(id) });

  return (
    <div>
      <p>
        <Link to="/evaluations">← Evaluations</Link>
      </p>
      <QueryBoundary query={query}>{(ev) => <Detail ev={ev} />}</QueryBoundary>
    </div>
  );
}

function Detail({ ev }: { ev: Eval }) {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const sensitive = "input" in ev; // read_sensitive fields present

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>
          Evaluation <span className="mono">{ev.id.slice(0, 8)}</span>
        </h1>
        <StatusBadge status={ev.status} />
      </div>

      <div className="card">
        <div className="stat-grid">
          <Field label="Decision" value={ev.decision} mono />
          <Field label="Version" value={ev.version ?? "—"} />
          <Field label="External ID" value={ev.external_id ?? "—"} mono />
          <Field label="Outcome" value={ev.outcome ?? "—"} />
          <Field label="Decided by" value={ev.decided_by ? titleCase(ev.decided_by) : "—"} />
          <Field label="Route reason" value={ev.route_reason ? titleCase(ev.route_reason) : "—"} />
          <Field label="Confidence" value={fmtConfidence(ev.model?.raw_confidence)} />
          <Field label="Created" value={fmtDateTime(ev.created_at)} />
          <Field label="Decided" value={fmtDateTime(ev.decided_at)} />
        </div>
      </div>

      {ev.model && (
        <section className="card">
          <h2>Model</h2>
          <div className="stat-grid">
            <Field label="Suggested" value={ev.model.suggested_outcome ?? "—"} />
            <Field label="Raw confidence" value={fmtConfidence(ev.model.raw_confidence)} />
            <Field label="Calibrated" value={fmtConfidence(ev.model.calibrated_confidence)} />
            <Field label="Threshold" value={fmtConfidence(ev.model.threshold)} />
          </div>
          {sensitive && ev.model_rationale && (
            <p className="muted" style={{ marginTop: "0.5rem" }}>
              {ev.model_rationale}
            </p>
          )}
        </section>
      )}

      {ev.review && (
        <section className="card">
          <h2>Review</h2>
          <p>
            <StatusBadge status={ev.review.status} /> {titleCase(ev.review.kind)} · due{" "}
            {fmtDateTime(ev.review.due_at)}
          </p>
        </section>
      )}

      {sensitive && (
        <section className="card">
          <h2>Screen flags</h2>
          {ev.screen_flags && ev.screen_flags.length > 0 ? (
            <div className="row">
              {ev.screen_flags.map((f) => (
                <Badge key={f} variant="warn">
                  {titleCase(f)}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="muted">None.</p>
          )}
        </section>
      )}

      {sensitive && (
        <section className="card">
          <h2>Input</h2>
          {ev.input ? (
            <JsonView value={ev.input} />
          ) : (
            <p className="muted">Input is not available (purged or not stored).</p>
          )}
        </section>
      )}

      {sensitive && (
        <section className="card">
          <h2>Rule trace</h2>
          {ev.rule_trace ? (
            <JsonView value={ev.rule_trace} defaultOpen={false} />
          ) : (
            <p className="muted">No rule trace.</p>
          )}
        </section>
      )}

      <ModelCalls id={ev.id} canSee={sensitive} />
      <Timeline id={ev.id} />

      {isAdmin && <AdminPanel ev={ev} />}
    </>
  );
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value" style={mono ? { fontFamily: "var(--mono)", fontSize: "1rem" } : undefined}>
        {value}
      </div>
    </div>
  );
}

function Timeline({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["evaluation", id, "events"], queryFn: () => evaluations.events(id) });
  return (
    <section className="card">
      <h2>Audit timeline</h2>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.results.length === 0 ? (
        <p className="muted">No events.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Seq</th>
              <th>Event</th>
              <th>When</th>
              <th>Actor</th>
            </tr>
          </thead>
          <tbody>
            {query.data.results.map((e) => (
              <tr key={e.seq}>
                <td className="mono">{e.seq}</td>
                <td>{e.event_type}</td>
                <td>{fmtDateTime(e.occurred_at)}</td>
                <td className="muted">{e.actor_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ModelCalls({ id, canSee }: { id: string; canSee: boolean }) {
  const query = useQuery({
    queryKey: ["evaluation", id, "model-calls"],
    queryFn: () => evaluations.modelCalls(id),
    enabled: canSee,
  });
  if (!canSee) return null;
  return (
    <section className="card">
      <h2>Model calls</h2>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.results.length === 0 ? (
        <p className="muted">No model calls.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Purpose</th>
              <th>Model</th>
              <th>Valid</th>
              <th>Tokens (in/out)</th>
              <th>Latency</th>
            </tr>
          </thead>
          <tbody>
            {query.data.results.map((c) => (
              <tr key={c.id}>
                <td>{titleCase(c.purpose)}</td>
                <td className="mono">{c.model_reported ?? c.model_requested}</td>
                <td>{c.valid === null ? "—" : c.valid ? "yes" : "no"}</td>
                <td className="mono">
                  {fmtNumber(c.input_tokens)} / {fmtNumber(c.output_tokens)}
                </td>
                <td className="mono">{c.latency_ms ? `${c.latency_ms} ms` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AdminPanel({ ev }: { ev: Eval }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [scope, setScope] = useState<"input" | "all">("input");
  const [reason, setReason] = useState<"erasure_request" | "data_error" | "other">("erasure_request");

  const legalHold = useMutation({
    mutationFn: (hold: boolean) => evaluations.legalHold(ev.id, hold),
    onSuccess: (r) => {
      setMsg(`Legal hold ${r.legal_hold ? "set" : "cleared"}.`);
      void qc.invalidateQueries({ queryKey: ["evaluation", ev.id] });
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Failed."),
  });

  const purge = useMutation({
    mutationFn: () => evaluations.purge(ev.id, { scope, reason }),
    onSuccess: () => {
      setMsg("Purge completed.");
      void qc.invalidateQueries({ queryKey: ["evaluation", ev.id] });
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Purge failed."),
  });

  const held = ev.legal_hold ?? false;

  return (
    <section className="card">
      <h2>Admin</h2>
      {msg && (
        <div className="banner" role="status">
          {msg}
        </div>
      )}
      <div className="row" style={{ marginBottom: "1rem" }}>
        <span>
          Legal hold: <Badge variant={held ? "warn" : "neutral"}>{held ? "on" : "off"}</Badge>
        </span>
        <button type="button" onClick={() => legalHold.mutate(!held)} disabled={legalHold.isPending}>
          {held ? "Clear hold" : "Place hold"}
        </button>
      </div>
      <div className="row">
        <label className="field" style={{ margin: 0 }}>
          <span>Purge scope</span>
          <select value={scope} onChange={(e) => setScope(e.target.value as "input" | "all")}>
            <option value="input">Input only</option>
            <option value="all">Input + explanation</option>
          </select>
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Reason</span>
          <select
            value={reason}
            onChange={(e) =>
              setReason(e.target.value as "erasure_request" | "data_error" | "other")
            }
          >
            <option value="erasure_request">Erasure request</option>
            <option value="data_error">Data error</option>
            <option value="other">Other</option>
          </select>
        </label>
        <button
          type="button"
          className="primary"
          onClick={() => {
            if (confirm("Purge is irreversible. Continue?")) purge.mutate();
          }}
          disabled={purge.isPending || held}
          title={held ? "Clear the legal hold first" : undefined}
        >
          Purge
        </button>
      </div>
    </section>
  );
}
