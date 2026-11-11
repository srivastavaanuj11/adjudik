import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { decisions } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { EmptyState, QueryBoundary } from "../../ui/states";
import { Badge } from "../../ui/components";
import { useVersion } from "./useVersion";
import type { RenderedPrompt, VersionSummary } from "../../api/types";

export function ModelStepTab({
  decisionKey,
  active,
}: {
  decisionKey: string;
  active: VersionSummary | null;
}) {
  const version = useVersion(decisionKey, active?.version);
  const [prompt, setPrompt] = useState<RenderedPrompt | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const render = useMutation({
    mutationFn: (sample: unknown) => decisions.renderPrompt(decisionKey, active!.version, sample),
    onSuccess: (p) => {
      setPrompt(p);
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not render the prompt."),
  });

  if (!active) return <EmptyState>No active version.</EmptyState>;

  return (
    <QueryBoundary query={version}>
      {(v) => {
        const ms = v.definition.model_step;
        if (!ms) return <EmptyState>This decision has no model step.</EmptyState>;
        const sample = (v.definition.sample_input as unknown) ?? {};
        return (
          <div>
            <section className="card">
              <h2>Model step</h2>
              <div className="stat-grid">
                <div className="stat">
                  <div className="label">Provider</div>
                  <div className="value" style={{ fontSize: "1rem" }}>
                    {ms.provider}
                  </div>
                </div>
                <div className="stat">
                  <div className="label">Model</div>
                  <div className="value mono" style={{ fontSize: "1rem" }}>
                    {ms.model}
                  </div>
                </div>
              </div>
              {ms.fields && ms.fields.length > 0 && (
                <p className="row" style={{ marginTop: "0.6rem" }}>
                  <span className="muted">Fields sent:</span>
                  {ms.fields.map((f) => (
                    <Badge key={f}>{f}</Badge>
                  ))}
                </p>
              )}
              {ms.policy_text && (
                <>
                  <h3>Policy text</h3>
                  <pre className="code">{ms.policy_text}</pre>
                </>
              )}
            </section>

            <section className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>Prompt preview</h2>
                <button
                  type="button"
                  onClick={() => render.mutate(sample)}
                  disabled={render.isPending}
                >
                  {render.isPending ? "Rendering…" : "Render sample prompt"}
                </button>
              </div>
              {err && (
                <div className="banner danger" role="alert">
                  {err}
                </div>
              )}
              {prompt && (
                <>
                  <h3>System</h3>
                  <pre className="code">{prompt.system}</pre>
                  <h3>User</h3>
                  <pre className="code">{prompt.user}</pre>
                </>
              )}
            </section>
          </div>
        );
      }}
    </QueryBoundary>
  );
}
