import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import type { Role } from "../api/types";
import { canAccess, landingFor } from "./nav";
import { Loading } from "../ui/states";

/** Requires a logged-in session; otherwise redirect to /login (or /password). */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading, mustChangePassword } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Loading…" />;
  if (mustChangePassword) return <Navigate to="/password" replace />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

/** Requires the active role to be one of `roles`; else bounces to the landing page. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { role } = useAuth();
  if (!canAccess(roles, role)) return <Navigate to={landingFor(role)} replace />;
  return <>{children}</>;
}
