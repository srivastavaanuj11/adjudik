import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { auth } from "../api/endpoints";
import type { Me, Role } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  /** True when the server reported password_change_required at login. */
  mustChangePassword: boolean;
  role: Role | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (newPassword: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [mustChangePassword, setMustChange] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await auth.me();
      setMe(data);
      setMustChange(false);
    } catch (err) {
      setMe(null);
      if (err instanceof ApiError && err.code === "password_change_required") {
        setMustChange(true);
      }
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await refresh();
      setLoading(false);
    })();
  }, [refresh]);

  const doLogin = useCallback(async (username: string, password: string) => {
    try {
      const data = await auth.login(username, password);
      setMe(data);
      setMustChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === "password_change_required") {
        setMustChange(true);
        setMe(null);
        return;
      }
      throw err;
    }
  }, []);

  const doLogout = useCallback(async () => {
    await auth.logout();
    setMe(null);
    setMustChange(false);
  }, []);

  const doChangePassword = useCallback(
    async (newPassword: string) => {
      await auth.changePassword(newPassword);
      setMustChange(false);
      await refresh();
    },
    [refresh],
  );

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      mustChangePassword,
      role: me?.role ?? null,
      login: doLogin,
      logout: doLogout,
      changePassword: doChangePassword,
      refresh,
    }),
    [me, loading, mustChangePassword, doLogin, doLogout, doChangePassword, refresh],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
