"use client";

/**
 * Who's logged in, app-wide.
 *
 * One fetch to `/auth/me` on mount decides it: the session cookie is
 * httpOnly (JS can't read it directly), so this is the only way the
 * frontend finds out whether it's carrying a valid one. A 401 here just
 * means "not logged in" -- not an error worth surfacing.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { ApiError, type Profile, me as fetchMe } from "@/lib/api";

type AuthContextValue = {
  user: Profile | null;
  /** True only until the first `/auth/me` call resolves -- lets the guard
   * below tell "still checking" apart from "checked, nobody's logged in". */
  loading: boolean;
  /** Re-run after login/signup/logout so the rest of the app sees the change
   * without a full page reload. */
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const profile = await fetchMe();
      setUser(profile);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) {
        console.error("[auth] /auth/me failed:", err);
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, loading, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
