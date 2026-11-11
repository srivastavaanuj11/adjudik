import { useEffect, useRef, useState } from "react";
import { Badge, JsonView } from "../../ui/components";
import { fmtCountdown, titleCase } from "../../ui/format";
import type { ReviewCompleteBody, ReviewDetail } from "../../api/types";

const SUGGESTION_LABEL = "Model-generated; may be shaped by the case text";

export interface ReviewBodyProps {
  task: ReviewDetail;
  busy: boolean;
  /** Server-side error from the last complete attempt (e.g. review_invalid). */
  serverError?: string | null;
  autoAdvance: boolean;
  onAutoAdvanceChange: (v: boolean) => void;
  onComplete: (body: ReviewCompleteBody) => void;
  onRelease: () => void;
  /** Seconds remaining on the lease; null hides the countdown. */
  leaseExpiresAt: string | null;
}

/**
 * The review workspace body (SPEC §12.2). Pure w.r.t. the network: it takes a
 * claimed task and raises onComplete/onRelease. Blind tasks (suggestion === null)
 * never render a suggestion; the suggestion card never shows a confidence or
 * threshold (the API omits them by design).
 */
export function ReviewBody({
  task,
  busy,
  serverError,
  autoAdvance,
  onAutoAdvanceChange,
  onComplete,
  onRelease,
  leaseExpiresAt,
}: ReviewBodyProps) {
  const outcomes = task.decision.outcomes;
  const reasonCodes = task.decision.reason_codes;
  const suggestionVisible = task.suggestion !== null;

  const [selected, setSelected] = useState<string | null>(null);
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [reasonText, setReasonText] = useState("");
  const [notes, setNotes] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);

  function actionFor(outcome: string): "accept" | "override" | "decide" {
    if (!suggestionVisible) return "decide";
    return outcome === task.suggestion?.outcome ? "accept" : "override";
  }

  function build(outcome: string | null): ReviewCompleteBody | null {
    if (!outcome) {
      setLocalError("Select an outcome.");
      return null;
    }
    const action = actionFor(outcome);
    if (action === "override" && reasonText.trim() === "") {
      setLocalError("A reason is required when you override the suggestion.");
      return null;
    }
    if (reasonCodes.length > 0 && codes.size === 0) {
      setLocalError("Select at least one reason code.");
      return null;
    }
    setLocalError(null);
    return {
      action,
      outcome,
      reason_codes: [...codes],
      reason_text: reasonText.trim() || null,
      notes: notes.trim() || null,
    };
  }

  function submit(outcome: string | null) {
    const body = build(outcome);
    if (body) onComplete(body);
  }

  function acceptSuggestion() {
    if (!task.suggestion?.outcome) return;
    setSelected(task.suggestion.outcome);
    submit(task.suggestion.outcome);
  }

  // Keyboard shortcuts, active only when focus is outside a text field (§12.2).
  useEffect(() => {
    function inTextField(): boolean {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return false;
      if (el.tagName === "TEXTAREA") return true;
      if (el.tagName === "INPUT") {
        const t = (el as HTMLInputElement).type;
        return t === "text" || t === "number" || t === "password" || t === "search";
      }
      return false;
    }

    function onKey(e: KeyboardEvent) {
      if (busy) return;
      if (e.key === "Escape") {
        if (inTextField()) {
          (document.activeElement as HTMLElement).blur();
        } else {
          onRelease();
        }
        return;
      }
      if (inTextField()) return;

      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        submit(selected);
        return;
      }
      if (/^[1-9]$/.test(e.key)) {
        const idx = Number(e.key) - 1;
        if (idx < outcomes.length) {
          e.preventDefault();
          setSelected(outcomes[idx].id);
        }
        return;
      }
      if ((e.key === "a" || e.key === "A") && suggestionVisible) {
        e.preventDefault();
        acceptSuggestion();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, selected, outcomes, suggestionVisible, reasonText, codes, notes]);

  const d = task.decision;

  return (
    <div ref={rootRef}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "start" }}>
        <div>
          <h1 style={{ marginBottom: "0.25rem" }}>
            {d.name} <span className="muted" style={{ fontWeight: 400 }}>v{d.version}</span>
          </h1>
          <div className="row">
            <Badge variant={task.kind === "gate" ? "warn" : "neutral"}>{titleCase(task.kind)}</Badge>
            <Badge>{titleCase(task.reason)}</Badge>
            {task.case.external_id && <span className="mono">{task.case.external_id}</span>}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="muted">Due {fmtCountdown(task.due_at)}</div>
          {leaseExpiresAt && <div className="muted">Lease {fmtCountdown(leaseExpiresAt)}</div>}
        </div>
      </div>

      <section className="card">
        <h2>Case data</h2>
        <JsonView value={task.case.input} highlight={new Set(task.case.included_paths)} />
        <p className="muted" style={{ marginTop: "0.5rem" }}>
          Fields marked “◂ model” were sent to the model.
        </p>
      </section>

      <section className="card">
        <h2>Rule trace</h2>
        {task.rules.inputs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Input</th>
                <th>Expression</th>
              </tr>
            </thead>
            <tbody>
              {task.rules.inputs.map((i) => (
                <tr key={i.id}>
                  <td className="mono">{i.id}</td>
                  <td className="mono muted">{i.expr}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ marginTop: "0.5rem" }}>
          <JsonView value={task.rules.trace} defaultOpen={false} />
        </div>
        {!suggestionVisible && (
          <p className="muted">No automatable rule matched; this case was sent for review.</p>
        )}
      </section>

      {task.suggestion && (
        <section className="card" aria-label="model suggestion">
          <h2>Suggestion</h2>
          <p className="row">
            <Badge variant="ok">{task.suggestion.outcome}</Badge>
          </p>
          {task.suggestion.rationale && (
            <p style={{ whiteSpace: "pre-wrap" }}>{task.suggestion.rationale}</p>
          )}
          <p className="muted">{SUGGESTION_LABEL}</p>
        </section>
      )}

      {task.prior_evaluations.length > 0 && (
        <section className="card">
          <h2>Prior evaluations</h2>
          <ul>
            {task.prior_evaluations.map((p) => (
              <li key={p.id} className="mono">
                {p.id.slice(0, 8)} · {p.status}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card" aria-label="decision">
        <h2>Decision</h2>
        {(localError || serverError) && (
          <div className="banner danger" role="alert">
            {localError ?? serverError}
          </div>
        )}

        <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="muted">Outcome</legend>
          {outcomes.map((o, i) => (
            <label key={o.id} className="row" style={{ gap: "0.4rem" }}>
              <input
                type="radio"
                name="outcome"
                checked={selected === o.id}
                onChange={() => setSelected(o.id)}
              />
              <span>
                <kbd className="mono">{i + 1}</kbd> {o.label}
                {task.suggestion?.outcome === o.id && (
                  <span className="muted"> · suggested</span>
                )}
              </span>
            </label>
          ))}
        </fieldset>

        {reasonCodes.length > 0 && (
          <fieldset style={{ border: "none", padding: 0, margin: "0.75rem 0 0" }}>
            <legend className="muted">Reason codes</legend>
            {reasonCodes.map((c) => (
              <label key={c.id} className="row" style={{ gap: "0.4rem" }}>
                <input
                  type="checkbox"
                  checked={codes.has(c.id)}
                  onChange={(e) =>
                    setCodes((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(c.id);
                      else next.delete(c.id);
                      return next;
                    })
                  }
                />
                {c.label}
              </label>
            ))}
          </fieldset>
        )}

        <label className="field" style={{ marginTop: "0.75rem" }}>
          <span>
            Reason {selected && actionFor(selected) === "override" ? "(required for override)" : "(optional)"}
          </span>
          <textarea
            value={reasonText}
            onChange={(e) => setReasonText(e.target.value)}
            rows={2}
            style={{ width: "100%" }}
          />
        </label>
        <label className="field">
          <span>Notes (optional)</span>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} style={{ width: "100%" }} />
        </label>

        <div className="row" style={{ marginTop: "0.5rem" }}>
          {suggestionVisible && (
            <button type="button" onClick={acceptSuggestion} disabled={busy}>
              Accept suggestion <kbd className="mono">A</kbd>
            </button>
          )}
          <button type="button" className="primary" onClick={() => submit(selected)} disabled={busy}>
            Submit <kbd className="mono">⌘↵</kbd>
          </button>
          <button type="button" onClick={onRelease} disabled={busy}>
            Release <kbd className="mono">Esc</kbd>
          </button>
          <label className="row" style={{ gap: "0.4rem", marginLeft: "auto" }}>
            <input
              type="checkbox"
              checked={autoAdvance}
              onChange={(e) => onAutoAdvanceChange(e.target.checked)}
            />
            Auto-advance
          </label>
        </div>
      </section>
    </div>
  );
}
