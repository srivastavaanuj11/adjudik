import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { decisions } from "../../api/endpoints";
import { useAuth } from "../../auth/AuthContext";
import { EmptyState, QueryBoundary } from "../../ui/states";
import { Badge } from "../../ui/components";
import { fmtNumber, fmtPercent } from "../../ui/format";
import { useVersion } from "./useVersion";
import type { Policy, VersionSummary } from "../../api/types";

function sevenDaysAgo(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 10);
}

export function OverviewTab({
  decisionKey,
  active,
}: {
  decisionKey: string;
  active: VersionSummary | null;
}) {
  const { role } = useAuth();
  const qc = useQueryClient();
  const version = useVersion(decisionKey, active?.version);
  const metrics = useQuery({
    queryKey: ["decision", decisionKey, "metrics", "7d"],
    queryFn: () => decisions.metrics(decisionKey, { from: sevenDaysAgo() }),
    staleTime: 60_000,
  });
  const policy = useQuery({
    queryKey: ["decision", decisionKey, "version", active?.version, "policy"],
    queryFn: () => decisions.activePolicy(decisionKey, active!.version),
    enabled: !!active,
  });

  const ack = useMutation({
    mutationFn: () => decisions.acknowledgeModel(decisionKey, active!.version),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["decision", decisionKey] }),
  });

  if (!active) return <EmptyState>No versions yet. Create a draft via the API or CLI.</EmptyState>;

  return (
    <div>
      <QueryBoundary query={version}>
        {(v) => {
          const outcomes = v.definition.outcomes ?? [];
          const observed = v.observed_model;
          const changed = observed && v.definition.model_step?.model && observed !== v.definition.model_step.model;
          return (
            <>
              <section className="card">
                <h2>Active version {v.version}</h2>
                <div className="row">
                  <span>
                    Outcomes:{" "}
                    {outcomes.map((o) => (
                      <Badge key={o.id}>{o.label}</Badge>
                    ))}
                  </span>
                </div>
                {observed && (
                  <p className="row" style={{ marginTop: "0.5rem" }}>
                    <span>
                      Observed model: <span className="mono">{observed}</span>
                    </span>
                    {changed && (
                      <>
                        <Badge variant="warn">changed</Badge>
                        {role === "admin" && (
                          <button type="button" onClick={() => ack.mutate()} disabled={ack.isPending}>
                            Acknowledge
                          </button>
                        )}
                      </>
                    )}
                  </p>
                )}
              </section>

              <section className="card">
                <h2>Last 7 days</h2>
                {metrics.isSuccess ? (
                  <div className="stat-grid">
                    <div className="stat">
                      <div className="label">Volume</div>
                      <div className="value">{fmtNumber(metrics.data.volume)}</div>
                    </div>
                    <div className="stat">
                      <div className="label">Automation rate</div>
                      <div className="value">{fmtPercent(metrics.data.automation_rate)}</div>
                    </div>
                  </div>
                ) : (
                  <p className="muted">Loading metrics…</p>
                )}
              </section>

              <section className="card">
                <h2>Active automation policy</h2>
                <PolicySummary policy={normalizePolicy(policy.data)} loading={policy.isPending} />
              </section>
            </>
          );
        }}
      </QueryBoundary>
    </div>
  );
}

function normalizePolicy(data: { policy: Policy | null } | Policy | undefined): Policy | null {
  if (!data) return null;
  if ("policy" in data) return data.policy;
  return data;
}

function PolicySummary({ policy, loading }: { policy: Policy | null; loading: boolean }) {
  if (loading) return <p className="muted">Loading…</p>;
  if (!policy) return <p className="muted">No active automation policy — all cases go to review.</p>;
  return (
    <div>
      <p>
        Source: <Badge>{policy.source}</Badge> · audit sample {fmtPercent(policy.audit_sample_rate)}
      </p>
      <table>
        <thead>
          <tr>
            <th>Outcome</th>
            <th>Threshold</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(policy.thresholds).map(([k, t]) => (
            <tr key={k}>
              <td>{k}</td>
              <td className="mono">{t}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
