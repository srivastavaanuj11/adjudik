import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { landingFor } from "../app/nav";

export function Login() {
  const { me, role, login, mustChangePassword, loading } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (loading) return null;
  if (mustChangePassword) return <Navigate to="/password" replace />;
  if (me) return <Navigate to={landingFor(role)} replace />;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      // After login the auth state updates; navigate once role is known.
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "rate_limited") {
        setError("Too many attempts. Try again later.");
      } else {
        setError("Invalid username or password.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <h1>Adjudik</h1>
      <p className="muted">Sign in to the dashboard.</p>
      <form onSubmit={onSubmit}>
        {error && (
          <div className="banner danger" role="alert">
            {error}
          </div>
        )}
        <label className="field">
          <span>Username</span>
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
