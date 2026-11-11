import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { decisions } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Badge, StatusBadge } from "../../ui/components";
import { fmtDateTime } from "../../ui/format";
import { activeVersion } from "../DecisionDetail";
import type { VersionSummary } from "../../api/types";

export function VersionsTab({
  decisionKey,
  versions,
}: {
  decisionKey: string;
  versions: VersionSummary[];
}) {
  const { role } = useAuth();
  const qc = useQueryClient();
  const isAdmin = role === "admin";
  const active = activeVersion(versions);
  const [msg, setMsg] = useState<string | null>(null);
  const [publishing, setPublishing] = useState<number | null>(null);
  const [carryOver, setCarryOver] = useState(false);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["decision", decisionKey] });

  const publish = useMutation({
    mutationFn: (v: { n: number; carry: boolean }) =>
      decisions.publish(decisionKey, v.n, v.carry),
    onSuccess: () => {
      setMsg("Published.");
      setPublishing(null);
      invalidate();
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Publish failed."),
  });
  const retire = useMutation({
    mutationFn: (n: number) => decisions.retire(decisionKey, n),
    onSuccess: () => {
      setMsg("Retired.");
      invalidate();
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Retire failed."),
  });

  function canCarryOver(draft: VersionSummary): boolean {
    return (
      !!active &&
      active.model_step_fingerprint === draft.model_step_fingerprint &&
      active.rules_hash === draft.rules_hash
    );
  }

  return (
    <div>
      {msg && (
        <div className="banner" role="status">
          {msg}
        </div>
      )}
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Status</th>
              <th>Definition hash</th>
              <th>Rules hash</th>
              <th>Fingerprint</th>
              <th>Created</th>
              <th>Published</th>
              {isAdmin && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version}>
                <td>{v.version}</td>
                <td>
                  <StatusBadge status={v.status} />
                </td>
                <td className="mono">{v.definition_hash.slice(0, 10)}</td>
                <td className="mono">{v.rules_hash ? v.rules_hash.slice(0, 10) : "—"}</td>
                <td className="mono">{v.model_step_fingerprint.slice(0, 10)}</td>
                <td>{fmtDateTime(v.created_at)}</td>
                <td>{fmtDateTime(v.published_at)}</td>
                {isAdmin && (
                  <td>
                    {v.status === "draft" && (
                      <button
                        type="button"
                        onClick={() => {
                          setPublishing(v.version);
                          setCarryOver(false);
                        }}
                      >
                        Publish
                      </button>
                    )}
                    {v.status === "active" && (
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm(`Retire version ${v.version}?`)) retire.mutate(v.version);
                        }}
                      >
                        Retire
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {publishing !== null &&
        (() => {
          const draft = versions.find((v) => v.version === publishing)!;
          const carryOk = canCarryOver(draft);
          return (
            <div className="card">
              <h2>Publish version {publishing}</h2>
              <p className="muted">
                This activates version {publishing} and retires the current active version
                {active ? ` (v${active.version})` : ""}.
              </p>
              <ul>
                <li>
                  Fingerprint {carryOk || !active ? "" : "differs from active"}
                  {active && active.model_step_fingerprint === draft.model_step_fingerprint
                    ? " matches active"
                    : ""}
                </li>
                <li>
                  Rules hash{" "}
                  {active && active.rules_hash === draft.rules_hash ? "matches active" : "differs from active"}
                </li>
              </ul>
              <label className="row" style={{ gap: "0.4rem" }}>
                <input
                  type="checkbox"
                  checked={carryOver}
                  disabled={!carryOk}
                  onChange={(e) => setCarryOver(e.target.checked)}
                />
                Carry over the active policy{" "}
                {!carryOk && <Badge variant="neutral">unavailable — fingerprint/rules differ</Badge>}
              </label>
              <div className="row" style={{ marginTop: "0.75rem" }}>
                <button
                  type="button"
                  className="primary"
                  onClick={() => publish.mutate({ n: publishing, carry: carryOver })}
                  disabled={publish.isPending}
                >
                  Confirm publish
                </button>
                <button type="button" onClick={() => setPublishing(null)}>
                  Cancel
                </button>
              </div>
            </div>
          );
        })()}
    </div>
  );
}
