import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { decisions } from "../api/endpoints";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useInfiniteList } from "../ui/useInfiniteList";
import { Loading, ErrorState, EmptyState } from "../ui/states";
import { LoadMore } from "../ui/components";
import { fmtNumber, fmtPercent } from "../ui/format";

function sevenDaysAgo(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 10);
}

export function Decisions() {
  const { role } = useAuth();
  const navigate = useNavigate();
  const canCreate = role === "admin" || role === "author";
  const [showNew, setShowNew] = useState(false);

  const { query, rows } = useInfiniteList(["decisions"], (cursor) => decisions.list(cursor));

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Decisions</h1>
        {canCreate && (
          <button type="button" className="primary" onClick={() => setShowNew((v) => !v)}>
            New decision
          </button>
        )}
      </div>

      {showNew && <NewDecision onDone={() => setShowNew(false)} />}

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState>No decisions yet. Drafts are created through the API or CLI.</EmptyState>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Name</th>
                <th>Active version</th>
                <th>7-day volume</th>
                <th>Automation rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.key} className="clickable" onClick={() => navigate(`/decisions/${d.key}`)}>
                  <td className="mono">{d.key}</td>
                  <td>{d.name}</td>
                  <MetricsCells decisionKey={d.key} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <LoadMore
        hasMore={query.hasNextPage ?? false}
        loading={query.isFetchingNextPage}
        onMore={() => void query.fetchNextPage()}
      />
    </div>
  );
}

function MetricsCells({ decisionKey }: { decisionKey: string }) {
  const q = useQuery({
    queryKey: ["decision", decisionKey, "metrics", "7d"],
    queryFn: () => decisions.metrics(decisionKey, { from: sevenDaysAgo() }),
    staleTime: 60_000,
  });
  if (q.isPending) {
    return (
      <>
        <td className="muted">…</td>
        <td className="muted">…</td>
        <td className="muted">…</td>
      </>
    );
  }
  if (q.isError) {
    return (
      <>
        <td className="muted">—</td>
        <td className="muted">—</td>
        <td className="muted">—</td>
      </>
    );
  }
  return (
    <>
      <td>{q.data.version ?? <span className="muted">none</span>}</td>
      <td>{fmtNumber(q.data.volume)}</td>
      <td>{fmtPercent(q.data.automation_rate)}</td>
    </>
  );
}

function NewDecision({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => decisions.create({ key, name, description: description || undefined }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["decisions"] });
      onDone();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not create the decision."),
  });

  return (
    <div className="card">
      <h2>New decision</h2>
      {err && (
        <div className="banner danger" role="alert">
          {err}
        </div>
      )}
      <div className="row">
        <label className="field" style={{ margin: 0 }}>
          <span>Key</span>
          <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="refund_approval" />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field" style={{ margin: 0, flex: 1 }}>
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
      <div className="row" style={{ marginTop: "0.75rem" }}>
        <button
          type="button"
          className="primary"
          onClick={() => create.mutate()}
          disabled={create.isPending || !key || !name}
        >
          Create
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </div>
  );
}
