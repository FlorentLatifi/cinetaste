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
import { setSessionExpiredHandler, tryRefreshSession } from "../../api/client";
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
    const tokens = await tryRefreshSession();
    if (!tokens) return;
    const me = await authApi.getMe(tokens.access_token);
    setAccessTokenState(tokens.access_token);
    setUser(me);
  }, []);

  useEffect(() => {
    let cancelled = false;
    clearLegacyTokenStorage();

    async function bootstrap() {
      // Goes through the shared single-flight refresh: StrictMode's double
      // effect and other tabs reuse one request instead of racing it.
      const tokens = await tryRefreshSession();
      if (cancelled) return;
      if (tokens) {
        applySession(tokens);
      } else {
        setAccessToken(null);
        setAccessTokenState(null);
        setUser(null);
      }
      setLoading(false);
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [applySession]);

  // When apiFetch gets 401 and refresh fails, drop local session immediately.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setAccessToken(null);
      setAccessTokenState(null);
      setUser(null);
      clearLegacyTokenStorage();
    });
    return () => setSessionExpiredHandler(null);
  }, []);

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
