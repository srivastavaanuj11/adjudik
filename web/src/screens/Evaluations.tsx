import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { evaluations, type EvaluationFilters } from "../api/endpoints";
import { useInfiniteList } from "../ui/useInfiniteList";
import { Loading, ErrorState, EmptyState } from "../ui/states";
import { LoadMore, StatusBadge } from "../ui/components";
import { fmtConfidence, fmtDateTime, titleCase } from "../ui/format";

const STATUSES = ["processing", "pending_review", "decided", "cancelled"];
const DECIDED_BY = ["rule", "model", "human"];

export function Evaluations() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<EvaluationFilters>({});

  const { query, rows } = useInfiniteList(["evaluations", filters], (cursor) =>
    evaluations.list({ ...filters, cursor }),
  );

  function set<K extends keyof EvaluationFilters>(k: K, v: EvaluationFilters[K]) {
    setFilters((f) => ({ ...f, [k]: v || undefined }));
  }

  return (
    <div>
      <h1>Evaluations</h1>
      <div className="toolbar">
        <label className="field" style={{ margin: 0 }}>
          <span>Decision</span>
          <input onChange={(e) => set("decision", e.target.value)} placeholder="decision key" />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Status</span>
          <select onChange={(e) => set("status", e.target.value)}>
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Decided by</span>
          <select onChange={(e) => set("decided_by", e.target.value)}>
            <option value="">Any</option>
            {DECIDED_BY.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>External ID</span>
          <input onChange={(e) => set("external_id", e.target.value)} placeholder="external id" />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Route reason</span>
          <input onChange={(e) => set("route_reason", e.target.value)} placeholder="route reason" />
        </label>
      </div>

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState>No evaluations match these filters.</EmptyState>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Created</th>
                <th>Decision</th>
                <th>External ID</th>
                <th>Status</th>
                <th>Outcome</th>
                <th>Decided by</th>
                <th>Confidence</th>
                <th>Route reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className="clickable" onClick={() => navigate(`/evaluations/${e.id}`)}>
                  <td>{fmtDateTime(e.created_at)}</td>
                  <td className="mono">{e.decision}</td>
                  <td className="mono">{e.external_id ?? <span className="muted">—</span>}</td>
                  <td>
                    <StatusBadge status={e.status} />
                  </td>
                  <td>{e.outcome ?? <span className="muted">—</span>}</td>
                  <td>{e.decided_by ? titleCase(e.decided_by) : <span className="muted">—</span>}</td>
                  <td className="mono">{fmtConfidence(e.model?.raw_confidence)}</td>
                  <td className="muted">{e.route_reason ? titleCase(e.route_reason) : "—"}</td>
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
