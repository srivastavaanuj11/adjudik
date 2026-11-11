import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { audit } from "../api/endpoints";
import { ApiError } from "../api/client";
import { Badge, LoadMore } from "../ui/components";
import { useInfiniteList } from "../ui/useInfiniteList";
import { Loading, ErrorState } from "../ui/states";
import { fmtDateTime } from "../ui/format";
import type { AuditVerification } from "../api/types";

const MODES = ["incremental", "full", "reconcile"];

export function Audit() {
  return (
    <div>
      <h1>Audit</h1>
      <Verification />
      <Checkpoints />
      <ExportPanel />
      <Events />
    </div>
  );
}

function normalizeVerification(
  data: { verification: AuditVerification | null } | AuditVerification | undefined,
): AuditVerification | null {
  if (!data) return null;
  if ("verification" in data) return data.verification;
  return data;
}

function Verification() {
  const qc = useQueryClient();
  const latest = useQuery({
    queryKey: ["audit", "latest"],
    queryFn: () => audit.latestVerification(),
  });
  const [mode, setMode] = useState("incremental");
  const run = useMutation({
    mutationFn: () => audit.runVerification(mode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit", "latest"] }),
  });

  const v = normalizeVerification(latest.data);
  return (
    <section className="card">
      <h2>Verification</h2>
      <div className="row" style={{ marginBottom: "0.75rem" }}>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <button type="button" className="primary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Running…" : "Run verification"}
        </button>
        {run.isError && (
          <span className="danger">
            {run.error instanceof ApiError ? run.error.message : "Failed"}
          </span>
        )}
      </div>
      {latest.isPending ? (
        <Loading />
      ) : !v ? (
        <p className="muted">No verification has been run yet.</p>
      ) : (
        <p className="row">
          <span>Latest: {v.mode}</span>
          <Badge variant={v.ok === true ? "ok" : v.ok === false ? "danger" : "warn"}>
            {v.ok === true ? "verified" : v.ok === false ? "FAILED" : v.status}
          </Badge>
          <span className="muted">
            seq {v.from_seq ?? 0}–{v.head_seq ?? "?"} · {fmtDateTime(v.finished_at ?? v.created_at)}
          </span>
          {v.first_bad_seq !== null && <span className="danger">first bad seq {v.first_bad_seq}</span>}
        </p>
      )}
    </section>
  );
}

function Checkpoints() {
  const query = useQuery({ queryKey: ["audit", "checkpoints"], queryFn: () => audit.checkpoints() });
  return (
    <section className="card" style={{ padding: 0 }}>
      <h2 style={{ padding: "0.75rem 1rem 0" }}>Checkpoints</h2>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.results.length === 0 ? (
        <p className="muted" style={{ padding: "0 1rem 1rem" }}>
          No checkpoints yet.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Seq</th>
              <th>Hash</th>
              <th>Key</th>
              <th>Created</th>
              <th>Anchored</th>
            </tr>
          </thead>
          <tbody>
            {query.data.results.map((c) => (
              <tr key={c.seq}>
                <td className="mono">{c.seq}</td>
                <td className="mono">{c.hash.slice(0, 16)}…</td>
                <td className="mono">{c.key_id}</td>
                <td>{fmtDateTime(c.created_at)}</td>
                <td>{c.anchored_at ? fmtDateTime(c.anchored_at) : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ExportPanel() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setErr(null);
    try {
      const text = await audit.export(from ? Number(from) : undefined, to ? Number(to) : undefined);
      const blob = new Blob([text], { type: "application/x-ndjson" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-export${from || to ? `-${from || 0}-${to || "head"}` : ""}.ndjson`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Export failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>NDJSON export</h2>
      <div className="row">
        <label className="field" style={{ margin: 0 }}>
          <span>From seq</span>
          <input type="number" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="0" />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>To seq</span>
          <input type="number" value={to} onChange={(e) => setTo(e.target.value)} placeholder="head" />
        </label>
        <button type="button" onClick={() => void download()} disabled={busy}>
          {busy ? "Exporting…" : "Download export"}
        </button>
      </div>
      {err && (
        <div className="banner danger" role="alert">
          {err}
        </div>
      )}
    </section>
  );
}

function Events() {
  const { query, rows } = useInfiniteList(["audit", "events"], (cursor) => audit.events(cursor));
  return (
    <section className="card" style={{ padding: 0 }}>
      <h2 style={{ padding: "0.75rem 1rem 0" }}>Event browser</h2>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : rows.length === 0 ? (
        <p className="muted" style={{ padding: "0 1rem 1rem" }}>
          No events.
        </p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Seq</th>
                <th>Event</th>
                <th>When</th>
                <th>Actor</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.seq}>
                  <td className="mono">{e.seq}</td>
                  <td>{e.event_type}</td>
                  <td>{fmtDateTime(e.occurred_at)}</td>
                  <td className="muted">{e.actor_type}</td>
                  <td className="mono">{e.hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: "0 1rem 1rem" }}>
            <LoadMore
              hasMore={query.hasNextPage ?? false}
              loading={query.isFetchingNextPage}
              onMore={() => void query.fetchNextPage()}
            />
          </div>
        </>
      )}
    </section>
  );
}
