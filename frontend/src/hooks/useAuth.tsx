import * as React from "react";

import { api, clearToken, getToken, setToken, type Me } from "@/lib/api";

interface AuthContextValue {
  isAuthenticated: boolean;
  loading: boolean;
  me: Me | null;
  isSuperadmin: boolean;
  canCreateUsers: boolean;
  login: (username: string, password: string) => Promise<void>;
  setSession: (token: string) => Promise<void>;
  refresh: () => Promise<void>;
  logout: () => void;
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = React.useState(false);
  const [me, setMe] = React.useState<Me | null>(null);
  const [loading, setLoading] = React.useState(true);

  const loadMe = React.useCallback(async () => {
    const profile = await api.me();
    setMe(profile);
    setIsAuthenticated(true);
  }, []);

  React.useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    loadMe()
      .catch(() => {
        clearToken();
        setIsAuthenticated(false);
        setMe(null);
      })
      .finally(() => setLoading(false));
  }, [loadMe]);

  const login = React.useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setToken(res.access_token);
    await loadMe();
  }, [loadMe]);

  const setSession = React.useCallback(async (token: string) => {
    setToken(token);
    await loadMe();
  }, [loadMe]);

  const logout = React.useCallback(() => {
    clearToken();
    setIsAuthenticated(false);
    setMe(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        loading,
        me,
        isSuperadmin: me?.role === "superadmin",
        canCreateUsers: !!me?.can_create_users,
        login,
        setSession,
        refresh: loadMe,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
