import { EmptyState, QueryBoundary } from "../../ui/states";
import { useVersion } from "./useVersion";
import type { VersionSummary } from "../../api/types";

export function RulesTab({
  decisionKey,
  active,
}: {
  decisionKey: string;
  active: VersionSummary | null;
}) {
  const version = useVersion(decisionKey, active?.version);
  if (!active) return <EmptyState>No active version.</EmptyState>;

  return (
    <QueryBoundary query={version}>
      {(v) => {
        const rules = v.definition.rules;
        if (!rules || !rules.rows || rules.rows.length === 0) {
          return <EmptyState>This decision has no rule table.</EmptyState>;
        }
        const inputs = rules.inputs ?? [];
        return (
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Row</th>
                  {inputs.map((i) => (
                    <th key={i.id} title={i.expr}>
                      {i.id}
                    </th>
                  ))}
                  <th>Outcome</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {rules.rows.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.id}</td>
                    {inputs.map((i) => (
                      <td key={i.id} className="mono">
                        {r.when?.[i.id] ?? "–"}
                      </td>
                    ))}
                    <td>{r.outcome ?? <span className="muted">—</span>}</td>
                    <td className="muted">{r.notes ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rules.hit_policy && (
              <p className="muted" style={{ padding: "0.5rem 0.6rem" }}>
                Hit policy: {rules.hit_policy}
              </p>
            )}
          </div>
        );
      }}
    </QueryBoundary>
  );
}
