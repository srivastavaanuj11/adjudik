import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { landingFor } from "../app/nav";

/** Forced password change on first login, and voluntary change (SPEC §12.1, §15.2). */
export function Password() {
  const { me, role, mustChangePassword, changePassword } = useAuth();
  const navigate = useNavigate();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  // Must be either in the forced-change state or already authenticated.
  if (!me && !mustChangePassword) return <Navigate to="/login" replace />;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrors([]);
    if (pw !== confirm) {
      setErrors(["Passwords do not match."]);
      return;
    }
    setBusy(true);
    try {
      await changePassword(pw);
      navigate(landingFor(role), { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.details.length) {
        setErrors(err.details.map((d) => d.message));
      } else if (err instanceof ApiError) {
        setErrors([err.message]);
      } else {
        setErrors(["Could not change password."]);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <h1>Set a new password</h1>
      {mustChangePassword && (
        <div className="banner" role="status">
          You must change your password before continuing.
        </div>
      )}
      <form onSubmit={onSubmit}>
        {errors.length > 0 && (
          <div className="banner danger" role="alert">
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {errors.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label className="field">
          <span>Confirm new password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Change password"}
        </button>
      </form>
    </div>
  );
}
