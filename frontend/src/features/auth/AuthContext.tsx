import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import * as authApi from "../../api/auth";
import type { User } from "../../api/auth";
import { tryRefreshAccessToken } from "../../api/client";
import { clearLegacyTokenStorage, setAccessToken } from "../../api/tokenStore";

type AuthState = {
  user: User | null;
  accessToken: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const applySession = useCallback((tokens: authApi.TokenResponse) => {
    setAccessToken(tokens.access_token);
    setAccessTokenState(tokens.access_token);
    setUser(tokens.user);
  }, []);

  const refreshUser = useCallback(async () => {
    const access = await tryRefreshAccessToken();
    if (!access) return;
    const me = await authApi.getMe(access);
    setAccessTokenState(access);
    setUser(me);
  }, []);

  useEffect(() => {
    let cancelled = false;
    clearLegacyTokenStorage();

    async function bootstrap() {
      try {
        // Session restore: httpOnly cookie → new access token
        const tokens = await authApi.refresh();
        if (cancelled) return;
        applySession(tokens);
      } catch {
        if (!cancelled) {
          setAccessToken(null);
          setAccessTokenState(null);
          setUser(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [applySession]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login({ email, password });
      applySession(tokens);
    },
    [applySession],
  );

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const tokens = await authApi.register({
        email,
        password,
        display_name: displayName,
      });
      applySession(tokens);
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Still clear local session
    }
    setAccessToken(null);
    setAccessTokenState(null);
    setUser(null);
    clearLegacyTokenStorage();
  }, []);

  const value = useMemo(
    () => ({ user, accessToken, loading, login, register, logout, refreshUser }),
    [user, accessToken, loading, login, register, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
