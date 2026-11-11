import { EmptyState, QueryBoundary } from "../../ui/states";
import { useVersion } from "./useVersion";
import type { VersionSummary } from "../../api/types";

export function DefinitionTab({
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
      {(v) => (
        <section className="card">
          <h2>
            Definition source{" "}
            <span className="muted" style={{ fontWeight: 400 }}>
              ({v.source_format})
            </span>
          </h2>
          <pre className="code" aria-label="definition source">
            {v.source_text}
          </pre>
        </section>
      )}
    </QueryBoundary>
  );
}
