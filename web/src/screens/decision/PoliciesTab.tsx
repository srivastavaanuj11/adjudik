import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { decisions, policies as policyApi } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { EmptyState, ErrorState, Loading } from "../../ui/states";
import { StatusBadge } from "../../ui/components";
import { fmtDateTime, fmtPercent } from "../../ui/format";
import { useVersion } from "./useVersion";
import type { Policy, VersionSummary } from "../../api/types";

export function PoliciesTab({
  decisionKey,
  active,
}: {
  decisionKey: string;
  active: VersionSummary | null;
}) {
  const { role } = useAuth();
  const qc = useQueryClient();
  const isAdmin = role === "admin";
  const version = useVersion(decisionKey, active?.version);
  const list = useQuery({
    queryKey: ["decision", decisionKey, "version", active?.version, "policies"],
    queryFn: () => decisions.policies(decisionKey, active!.version),
    enabled: !!active,
  });
  const [msg, setMsg] = useState<string | null>(null);

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ["decision", decisionKey, "version", active?.version] });

  const act = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: "approve" | "reject" | "deactivate" }) =>
      policyApi[kind](id),
    onSuccess: () => {
      setMsg("Done.");
      invalidate();
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Action failed."),
  });

  if (!active) return <EmptyState>No active version.</EmptyState>;

  return (
    <div>
      {msg && (
        <div className="banner" role="status">
          {msg}
        </div>
      )}

      {isAdmin && version.isSuccess && (
        <ProposeForm
          decisionKey={decisionKey}
          versionN={active.version}
          outcomes={(version.data.definition.outcomes ?? []).map((o) => o.id)}
          minAuditRate={0.05}
          onDone={() => {
            setMsg("Policy proposed.");
            invalidate();
          }}
        />
      )}

      <section className="card" style={{ padding: 0 }}>
        <h2 style={{ padding: "0.75rem 1rem 0" }}>History</h2>
        {list.isPending ? (
          <Loading />
        ) : list.isError ? (
          <ErrorState error={list.error} />
        ) : list.data.results.length === 0 ? (
          <EmptyState>No policies proposed for this version.</EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Source</th>
                <th>Thresholds</th>
                <th>Audit rate</th>
                <th>Proposed by</th>
                <th>Proposed</th>
                {isAdmin && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {list.data.results.map((p) => (
                <PolicyRow key={p.id} p={p} isAdmin={isAdmin} busy={act.isPending} onAct={act.mutate} />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function PolicyRow({
  p,
  isAdmin,
  busy,
  onAct,
}: {
  p: Policy;
  isAdmin: boolean;
  busy: boolean;
  onAct: (v: { id: string; kind: "approve" | "reject" | "deactivate" }) => void;
}) {
  return (
    <tr>
      <td>
        <StatusBadge status={p.status} />
      </td>
      <td>{p.source}</td>
      <td className="mono">
        {Object.entries(p.thresholds)
          .map(([k, v]) => `${k}:${v}`)
          .join("  ")}
      </td>
      <td>{fmtPercent(p.audit_sample_rate)}</td>
      <td className="muted">{p.proposed_by ?? "—"}</td>
      <td>{fmtDateTime(p.proposed_at)}</td>
      {isAdmin && (
        <td>
          {p.status === "proposed" && (
            <div className="row">
              <button type="button" onClick={() => onAct({ id: p.id, kind: "approve" })} disabled={busy}>
                Approve
              </button>
              <button type="button" onClick={() => onAct({ id: p.id, kind: "reject" })} disabled={busy}>
                Reject
              </button>
            </div>
          )}
          {p.status === "active" && (
            <button type="button" onClick={() => onAct({ id: p.id, kind: "deactivate" })} disabled={busy}>
              Deactivate
            </button>
          )}
        </td>
      )}
    </tr>
  );
}

function ProposeForm({
  decisionKey,
  versionN,
  outcomes,
  minAuditRate,
  onDone,
}: {
  decisionKey: string;
  versionN: number;
  outcomes: string[];
  minAuditRate: number;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [thresholds, setThresholds] = useState<Record<string, string>>({});
  const [auditRate, setAuditRate] = useState(String(minAuditRate));
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const propose = useMutation({
    mutationFn: () => {
      const parsed: Record<string, number> = {};
      for (const [k, v] of Object.entries(thresholds)) {
        if (v !== "") parsed[k] = Number(v);
      }
      return decisions.proposePolicy(decisionKey, versionN, {
        thresholds: parsed,
        audit_sample_rate: Number(auditRate),
        note: note || undefined,
      });
    },
    onSuccess: () => {
      setOpen(false);
      setThresholds({});
      setNote("");
      onDone();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        setErr(
          e.code === "four_eyes_required"
            ? "Proposed. A second admin must approve it (four-eyes)."
            : e.message,
        );
      } else {
        setErr("Could not propose the policy.");
      }
    },
  });

  if (!open) {
    return (
      <div style={{ marginBottom: "1rem" }}>
        <button type="button" className="primary" onClick={() => setOpen(true)}>
          Propose manual policy
        </button>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Propose manual policy</h2>
      {err && (
        <div className="banner" role="status">
          {err}
        </div>
      )}
      <p className="muted">
        Set a confidence threshold per outcome. Leave an outcome blank to always review it.
      </p>
      {outcomes.map((o) => (
        <label className="field" key={o}>
          <span>{o} threshold</span>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={thresholds[o] ?? ""}
            onChange={(e) => setThresholds((t) => ({ ...t, [o]: e.target.value }))}
            placeholder="e.g. 0.95"
          />
        </label>
      ))}
      <label className="field">
        <span>Audit sample rate</span>
        <input
          type="number"
          step="0.01"
          min="0"
          max="1"
          value={auditRate}
          onChange={(e) => setAuditRate(e.target.value)}
        />
      </label>
      <label className="field">
        <span>Note (optional)</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="row">
        <button
          type="button"
          className="primary"
          onClick={() => {
            setErr(null);
            propose.mutate();
          }}
          disabled={propose.isPending}
        >
          Propose
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
