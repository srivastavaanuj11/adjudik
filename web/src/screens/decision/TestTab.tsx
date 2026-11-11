import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { decisions } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { EmptyState, QueryBoundary } from "../../ui/states";
import { Badge, JsonView, StatusBadge } from "../../ui/components";
import { fmtConfidence, titleCase } from "../../ui/format";
import { useVersion } from "./useVersion";
import type { DryRunResult, VersionSummary } from "../../api/types";

export function TestTab({
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
        <Editor
          decisionKey={decisionKey}
          versionN={active.version}
          sample={(v.definition.sample_input as unknown) ?? {}}
        />
      )}
    </QueryBoundary>
  );
}

function Editor({
  decisionKey,
  versionN,
  sample,
}: {
  decisionKey: string;
  versionN: number;
  sample: unknown;
}) {
  const [text, setText] = useState(() => JSON.stringify(sample, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [result, setResult] = useState<DryRunResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setText(JSON.stringify(sample, null, 2));
  }, [sample]);

  const run = useMutation({
    mutationFn: (skipModel: boolean) => {
      let input: unknown;
      try {
        input = JSON.parse(text);
      } catch {
        throw new ApiError(0, "client_json", "The input is not valid JSON.");
      }
      return decisions.dryRun(decisionKey, versionN, { input, skip_model: skipModel });
    },
    onSuccess: (r) => {
      setResult(r);
      setErr(null);
    },
    onError: (e) => {
      setResult(null);
      setErr(e instanceof ApiError ? e.message : "Dry run failed.");
    },
  });

  function validateJson(v: string) {
    setText(v);
    try {
      JSON.parse(v);
      setParseError(null);
    } catch {
      setParseError("Invalid JSON");
    }
  }

  return (
    <div>
      <section className="card">
        <h2>Test input</h2>
        <textarea
          aria-label="test input JSON"
          className="mono"
          spellCheck={false}
          value={text}
          onChange={(e) => validateJson(e.target.value)}
          style={{ width: "100%", minHeight: 220, resize: "vertical" }}
        />
        {parseError && (
          <div className="banner danger" role="alert">
            {parseError}
          </div>
        )}
        <div className="row" style={{ marginTop: "0.5rem" }}>
          <button type="button" onClick={() => run.mutate(true)} disabled={!!parseError || run.isPending}>
            Rules only
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => run.mutate(false)}
            disabled={!!parseError || run.isPending}
          >
            Full run
          </button>
        </div>
      </section>

      {err && (
        <div className="banner danger" role="alert">
          {err}
        </div>
      )}

      {result && <DryRunView r={result} />}
    </div>
  );
}

function DryRunView({ r }: { r: DryRunResult }) {
  return (
    <section className="card">
      <h2>Result</h2>
      <div className="row" style={{ marginBottom: "0.6rem" }}>
        <StatusBadge status={r.status} />
        {r.outcome && <Badge variant="ok">{r.outcome}</Badge>}
        {r.decided_by && <span className="muted">by {titleCase(r.decided_by)}</span>}
        {r.route_reason && <Badge variant="warn">{titleCase(r.route_reason)}</Badge>}
      </div>

      {r.screen_flags.length > 0 && (
        <p className="row">
          <span className="muted">Screen flags:</span>
          {r.screen_flags.map((f) => (
            <Badge key={f} variant="warn">
              {titleCase(f)}
            </Badge>
          ))}
        </p>
      )}

      <h3>Rule result</h3>
      <p className="mono">
        {r.rule_result.type}
        {r.rule_result.outcome ? ` → ${r.rule_result.outcome}` : ""}
        {r.rule_result.matched_row_ids.length
          ? ` (rows ${r.rule_result.matched_row_ids.join(", ")})`
          : ""}
      </p>

      {r.model && (
        <>
          <h3>Model</h3>
          <p className="row">
            <span>Suggested: {r.model.suggested_outcome ?? "—"}</span>
            <span className="muted">confidence {fmtConfidence(r.model.raw_confidence)}</span>
            <span className="muted">
              {r.model.calls} call{r.model.calls === 1 ? "" : "s"}, {r.model.input_tokens}/
              {r.model.output_tokens} tokens
            </span>
          </p>
          {r.model.rationale && <p className="muted">{r.model.rationale}</p>}
        </>
      )}

      <h3>Trace</h3>
      <JsonView value={r.rule_trace} defaultOpen={false} />
    </section>
  );
}
