import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { members } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { Loading, ErrorState, EmptyState } from "../../ui/states";
import type { Role } from "../../api/types";

const ROLES: Role[] = ["admin", "author", "reviewer", "auditor"];

export function MembersPanel() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["members"], queryFn: () => members.list() });
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const [tempPw, setTempPw] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["members"] });

  const add = useMutation({
    mutationFn: () => members.create(username, role),
    onSuccess: (m) => {
      setTempPw(m.temporary_password ?? null);
      setUsername("");
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add the member."),
  });
  const changeRole = useMutation({
    mutationFn: ({ id, r }: { id: string; r: Role }) => members.update(id, r),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: string) => members.remove(id), onSuccess: invalidate });

  return (
    <div>
      {tempPw && (
        <div className="banner" role="status">
          Temporary password (shown once) — the member must change it on first login:
          <pre className="code" style={{ marginTop: "0.5rem" }}>
            {tempPw}
          </pre>
          <button type="button" onClick={() => setTempPw(null)}>
            Done
          </button>
        </div>
      )}

      <div className="card">
        <h2>Add member</h2>
        {err && (
          <div className="banner danger" role="alert">
            {err}
          </div>
        )}
        <div className="row">
          <label className="field" style={{ margin: 0 }}>
            <span>Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="primary"
            onClick={() => {
              setErr(null);
              add.mutate();
            }}
            disabled={add.isPending || !username}
          >
            Add
          </button>
        </div>
      </div>

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.results.length === 0 ? (
        <EmptyState>No members.</EmptyState>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Role</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {query.data.results.map((m) => (
                <tr key={m.id}>
                  <td>{m.username}</td>
                  <td>
                    <select
                      value={m.role}
                      onChange={(e) => changeRole.mutate({ id: m.id, r: e.target.value as Role })}
                      aria-label={`Role for ${m.username}`}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        if (confirm(`Remove ${m.username} from this workspace?`)) remove.mutate(m.id);
                      }}
                      disabled={remove.isPending}
                    >
                      Remove
                    </button>
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
