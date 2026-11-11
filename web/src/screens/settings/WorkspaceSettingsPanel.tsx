import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { workspace } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { QueryBoundary } from "../../ui/states";
import type { WorkspaceSettings } from "../../api/types";

export function WorkspaceSettingsPanel() {
  const query = useQuery({ queryKey: ["workspace-settings"], queryFn: () => workspace.getSettings() });
  return <QueryBoundary query={query}>{(s) => <Form initial={s} />}</QueryBoundary>;
}

function Form({ initial }: { initial: WorkspaceSettings }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<WorkspaceSettings>(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => setForm(initial), [initial]);

  const save = useMutation({
    mutationFn: () => workspace.patchSettings(form),
    onSuccess: (s) => {
      setForm(s);
      setMsg("Saved.");
      setErr(null);
      void qc.invalidateQueries({ queryKey: ["workspace-settings"] });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.message : "Save failed.");
      setMsg(null);
    },
  });

  function num(k: keyof WorkspaceSettings, v: string) {
    setForm((f) => ({ ...f, [k]: v === "" ? null : Number(v) }));
  }

  return (
    <div className="card" style={{ maxWidth: 520 }}>
      {msg && (
        <div className="banner" role="status">
          {msg}
        </div>
      )}
      {err && (
        <div className="banner danger" role="alert">
          {err}
        </div>
      )}
      <label className="field">
        <span>Minimum threshold (floor)</span>
        <input type="number" step="0.01" value={form.min_threshold} onChange={(e) => num("min_threshold", e.target.value)} />
      </label>
      <label className="field">
        <span>Minimum audit sample rate (floor)</span>
        <input
          type="number"
          step="0.01"
          value={form.min_audit_sample_rate}
          onChange={(e) => num("min_audit_sample_rate", e.target.value)}
        />
      </label>
      <label className="field">
        <span>Calibration blind rate</span>
        <input
          type="number"
          step="0.01"
          value={form.calibration_blind_rate}
          onChange={(e) => num("calibration_blind_rate", e.target.value)}
        />
      </label>
      <label className="field">
        <span>Reviewer claim mode</span>
        <select
          value={form.reviewer_claim_mode}
          onChange={(e) =>
            setForm((f) => ({ ...f, reviewer_claim_mode: e.target.value as WorkspaceSettings["reviewer_claim_mode"] }))
          }
        >
          <option value="any">Any (claim from the queue)</option>
          <option value="next_only">Next only (serve the next task)</option>
        </select>
      </label>
      <label className="row" style={{ gap: "0.4rem", marginBottom: "0.9rem" }}>
        <input
          type="checkbox"
          checked={form.require_four_eyes}
          onChange={(e) => setForm((f) => ({ ...f, require_four_eyes: e.target.checked }))}
        />
        Require four-eyes approval for policies and publishing
      </label>
      <label className="field">
        <span>Daily token budget (blank for none)</span>
        <input
          type="number"
          value={form.daily_token_budget ?? ""}
          onChange={(e) => num("daily_token_budget", e.target.value)}
        />
      </label>
      <button type="button" className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}
