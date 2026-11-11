import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiKeys } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { Badge } from "../../ui/components";
import { Loading, ErrorState, EmptyState } from "../../ui/states";
import { fmtDateTime } from "../../ui/format";
import type { ApiKeyScope } from "../../api/types";

const SCOPES: ApiKeyScope[] = ["evaluate", "read", "read_sensitive", "manage"];

export function ApiKeysPanel() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["api-keys"], queryFn: () => apiKeys.list() });
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ApiKeyScope[]>(["evaluate"]);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["api-keys"] });

  const create = useMutation({
    mutationFn: () => apiKeys.create({ name, scopes }),
    onSuccess: (k) => {
      setRevealed(k.key);
      setName("");
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not create the key."),
  });
  const rotate = useMutation({
    mutationFn: (id: string) => apiKeys.rotate(id),
    onSuccess: (k) => {
      setRevealed(k.key);
      invalidate();
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => apiKeys.revoke(id),
    onSuccess: invalidate,
  });

  function toggleScope(s: ApiKeyScope) {
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  }

  return (
    <div>
      {revealed && (
        <div className="banner" role="status">
          Copy this key now — it is shown only once:
          <pre className="code" style={{ marginTop: "0.5rem" }}>
            {revealed}
          </pre>
          <button type="button" onClick={() => setRevealed(null)}>
            Done
          </button>
        </div>
      )}

      <div className="card">
        <h2>Create API key</h2>
        {err && (
          <div className="banner danger" role="alert">
            {err}
          </div>
        )}
        <div className="row">
          <label className="field" style={{ margin: 0 }}>
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="muted">Scopes</legend>
            <div className="row">
              {SCOPES.map((s) => (
                <label key={s} className="row" style={{ gap: "0.3rem" }}>
                  <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
                  {s}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <div style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="primary"
            onClick={() => {
              setErr(null);
              create.mutate();
            }}
            disabled={create.isPending || !name || scopes.length === 0}
          >
            Create key
          </button>
        </div>
      </div>

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.results.length === 0 ? (
        <EmptyState>No API keys.</EmptyState>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Scopes</th>
                <th>Expires</th>
                <th>Last used</th>
                <th>State</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {query.data.results.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="mono">{k.prefix}</td>
                  <td>
                    {k.scopes.map((s) => (
                      <Badge key={s}>{s}</Badge>
                    ))}
                  </td>
                  <td>{fmtDateTime(k.expires_at)}</td>
                  <td>{fmtDateTime(k.last_used_at)}</td>
                  <td>
                    {k.revoked_at ? (
                      <Badge variant="danger">Revoked</Badge>
                    ) : (
                      <Badge variant="ok">Active</Badge>
                    )}
                  </td>
                  <td>
                    {!k.revoked_at && (
                      <div className="row">
                        <button type="button" onClick={() => rotate.mutate(k.id)} disabled={rotate.isPending}>
                          Rotate
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (confirm(`Revoke key "${k.name}"?`)) revoke.mutate(k.id);
                          }}
                          disabled={revoke.isPending}
                        >
                          Revoke
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
