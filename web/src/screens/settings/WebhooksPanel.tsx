import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { webhooks } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { useInfiniteList } from "../../ui/useInfiniteList";
import { Badge, LoadMore, StatusBadge } from "../../ui/components";
import { Loading, ErrorState, EmptyState } from "../../ui/states";
import { fmtDateTime } from "../../ui/format";
import type { WebhookEndpoint, WebhookEventType } from "../../api/types";

const EVENTS: WebhookEventType[] = [
  "evaluation.decided",
  "evaluation.pending_review",
  "evaluation.cancelled",
  "audit.disagreement",
  "automation.suspended",
];

export function WebhooksPanel() {
  const qc = useQueryClient();
  const { query, rows } = useInfiniteList(["webhooks"], () => webhooks.list());
  const [url, setUrl] = useState("");
  const [types, setTypes] = useState<WebhookEventType[]>(["evaluation.decided"]);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["webhooks"] });

  const create = useMutation({
    mutationFn: () => webhooks.create({ url, event_types: types }),
    onSuccess: (e) => {
      setRevealed(e.secret);
      setUrl("");
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not create the endpoint."),
  });
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => webhooks.update(id, { active }),
    onSuccess: invalidate,
  });
  const rotate = useMutation({
    mutationFn: (id: string) => webhooks.rotateSecret(id),
    onSuccess: (e) => setRevealed(e.secret),
  });

  return (
    <div>
      {revealed && (
        <div className="banner" role="status">
          Signing secret (shown once):
          <pre className="code" style={{ marginTop: "0.5rem" }}>
            {revealed}
          </pre>
          <button type="button" onClick={() => setRevealed(null)}>
            Done
          </button>
        </div>
      )}

      <div className="card">
        <h2>Add endpoint</h2>
        {err && (
          <div className="banner danger" role="alert">
            {err}
          </div>
        )}
        <label className="field">
          <span>URL (https)</span>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hooks" />
        </label>
        <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="muted">Event types</legend>
          <div className="row">
            {EVENTS.map((t) => (
              <label key={t} className="row" style={{ gap: "0.3rem" }}>
                <input
                  type="checkbox"
                  checked={types.includes(t)}
                  onChange={() =>
                    setTypes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))
                  }
                />
                {t}
              </label>
            ))}
          </div>
        </fieldset>
        <div style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="primary"
            onClick={() => {
              setErr(null);
              create.mutate();
            }}
            disabled={create.isPending || !url || types.length === 0}
          >
            Add endpoint
          </button>
        </div>
      </div>

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : rows.length === 0 ? (
        <EmptyState>No webhook endpoints.</EmptyState>
      ) : (
        rows.map((e) => (
          <EndpointCard
            key={e.id}
            endpoint={e}
            busy={toggle.isPending || rotate.isPending}
            onToggle={(active) => toggle.mutate({ id: e.id, active })}
            onRotate={() => rotate.mutate(e.id)}
          />
        ))
      )}
      <LoadMore
        hasMore={query.hasNextPage ?? false}
        loading={query.isFetchingNextPage}
        onMore={() => void query.fetchNextPage()}
      />
    </div>
  );
}

function EndpointCard({
  endpoint,
  busy,
  onToggle,
  onRotate,
}: {
  endpoint: WebhookEndpoint;
  busy: boolean;
  onToggle: (active: boolean) => void;
  onRotate: () => void;
}) {
  const [showLog, setShowLog] = useState(false);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="mono">{endpoint.url}</div>
          <div className="row" style={{ marginTop: "0.3rem" }}>
            <Badge variant={endpoint.active ? "ok" : "neutral"}>{endpoint.active ? "active" : "paused"}</Badge>
            {endpoint.event_types.map((t) => (
              <Badge key={t}>{t}</Badge>
            ))}
          </div>
        </div>
        <div className="row">
          <button type="button" onClick={() => onToggle(!endpoint.active)} disabled={busy}>
            {endpoint.active ? "Pause" : "Activate"}
          </button>
          <button type="button" onClick={onRotate} disabled={busy}>
            Rotate secret
          </button>
          <button type="button" onClick={() => setShowLog((v) => !v)}>
            {showLog ? "Hide log" : "Delivery log"}
          </button>
        </div>
      </div>
      {showLog && <DeliveryLog endpointId={endpoint.id} />}
    </div>
  );
}

function DeliveryLog({ endpointId }: { endpointId: string }) {
  const qc = useQueryClient();
  const { query, rows } = useInfiniteList(["webhook-deliveries", endpointId], (cursor) =>
    webhooks.deliveries(endpointId, cursor),
  );
  const retry = useMutation({
    mutationFn: (id: string) => webhooks.retryDelivery(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["webhook-deliveries", endpointId] }),
  });

  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} />;
  if (rows.length === 0) return <p className="muted">No deliveries yet.</p>;

  return (
    <div style={{ marginTop: "0.75rem" }}>
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Status</th>
            <th>Attempt</th>
            <th>Code</th>
            <th>Next attempt</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>{d.event_type}</td>
              <td>
                <StatusBadge status={d.status} />
              </td>
              <td className="mono">{d.attempt}</td>
              <td className="mono">{d.last_status_code ?? d.last_error_class ?? "—"}</td>
              <td>{d.next_attempt_at ? fmtDateTime(d.next_attempt_at) : "—"}</td>
              <td>{fmtDateTime(d.created_at)}</td>
              <td>
                {(d.status === "failed" || d.status === "dead") && (
                  <button type="button" onClick={() => retry.mutate(d.id)} disabled={retry.isPending}>
                    Retry
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <LoadMore
        hasMore={query.hasNextPage ?? false}
        loading={query.isFetchingNextPage}
        onMore={() => void query.fetchNextPage()}
      />
    </div>
  );
}
