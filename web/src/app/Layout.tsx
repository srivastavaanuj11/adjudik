import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { NAV, canAccess } from "./nav";
import { ThemeToggle } from "./ThemeToggle";

export function Layout() {
  const { me, role, logout } = useAuth();
  const navigate = useNavigate();
  const items = NAV.filter((n) => canAccess(n.roles, role));

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="brand">Adjudik</span>
        {me?.active_workspace && (
          <span className="badge" title="Active workspace">
            {me.active_workspace.name ?? me.active_workspace.slug}
          </span>
        )}
        <span className="spacer" />
        {role && <span className="muted">{role}</span>}
        {me && <span className="muted">· {me.username}</span>}
        <ThemeToggle />
        <button
          type="button"
          onClick={() => {
            void logout().then(() => navigate("/login"));
          }}
        >
          Sign out
        </button>
      </header>
      <nav className="app-nav" aria-label="Primary">
        {items.map((n) => (
          <NavLink key={n.to} to={n.to}>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
