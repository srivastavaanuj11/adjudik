import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { reviews } from "../api/endpoints";
import type { ReviewKind } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useInfiniteList } from "../ui/useInfiniteList";
import { Loading, ErrorState, EmptyState } from "../ui/states";
import { LoadMore, StatusBadge } from "../ui/components";
import { fmtAge, fmtCountdown, titleCase } from "../ui/format";

export function Reviews() {
  const { role } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canReview = role === "reviewer" || role === "admin";

  const [decision, setDecision] = useState("");
  const [kind, setKind] = useState<"" | ReviewKind>("");
  const [reason, setReason] = useState("");
  const [banner, setBanner] = useState<string | null>(null);

  const { query, rows } = useInfiniteList(
    ["reviews", { decision, kind, reason }],
    (cursor) =>
      reviews.list({
        decision: decision || undefined,
        kind: kind || undefined,
        reason: reason || undefined,
        cursor,
      }),
  );

  const startReviewing = useMutation({
    mutationFn: () => reviews.next({ decision: decision || undefined, kind: kind || undefined }),
    onSuccess: (task) => {
      if (task) {
        qc.setQueryData(["review", task.id], task);
        navigate(`/reviews/${task.id}`);
      } else {
        setBanner("No tasks are waiting in the queue.");
      }
    },
    onError: (err) => setBanner(err instanceof ApiError ? err.message : "Could not claim a task."),
  });

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Review queue</h1>
        {canReview && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setBanner(null);
              startReviewing.mutate();
            }}
            disabled={startReviewing.isPending}
          >
            {startReviewing.isPending ? "Claiming…" : "Start reviewing"}
          </button>
        )}
      </div>

      {banner && (
        <div className="banner" role="status">
          {banner}
        </div>
      )}

      <div className="toolbar">
        <label className="field" style={{ margin: 0 }}>
          <span>Decision</span>
          <input
            value={decision}
            onChange={(e) => setDecision(e.target.value)}
            placeholder="decision key"
          />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Kind</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as "" | ReviewKind)}>
            <option value="">Any</option>
            <option value="gate">Gate</option>
            <option value="audit">Audit</option>
          </select>
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span>Reason</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason" />
        </label>
      </div>

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState>The queue is empty.</EmptyState>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Due</th>
                <th>Decision</th>
                <th>External ID</th>
                <th>Kind</th>
                <th>Reason</th>
                <th>Status</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr
                  key={t.id}
                  className={canReview ? "clickable" : undefined}
                  onClick={canReview ? () => navigate(`/reviews/${t.id}`) : undefined}
                >
                  <td>{fmtCountdown(t.due_at)}</td>
                  <td className="mono">{t.decision}</td>
                  <td className="mono">{t.external_id ?? <span className="muted">hidden</span>}</td>
                  <td>{titleCase(t.kind)}</td>
                  <td>{titleCase(t.reason)}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>{fmtAge(t.created_at)}</td>
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
