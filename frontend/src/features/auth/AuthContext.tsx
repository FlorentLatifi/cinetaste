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

const ACCESS_KEY = "ct_access";
const REFRESH_KEY = "ct_refresh";

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

function persistTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const applySession = useCallback((tokens: authApi.TokenResponse) => {
    persistTokens(tokens.access_token, tokens.refresh_token);
    setAccessToken(tokens.access_token);
    setUser(tokens.user);
  }, []);

  const refreshUser = useCallback(async () => {
    const access = localStorage.getItem(ACCESS_KEY);
    if (!access) return;
    const me = await authApi.getMe(access);
    setUser(me);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      const access = localStorage.getItem(ACCESS_KEY);
      const refreshToken = localStorage.getItem(REFRESH_KEY);

      if (!access || !refreshToken) {
        if (!cancelled) setLoading(false);
        return;
      }

      try {
        const me = await authApi.getMe(access);
        if (!cancelled) {
          setAccessToken(access);
          setUser(me);
        }
      } catch {
        try {
          const tokens = await authApi.refresh(refreshToken);
          if (!cancelled) applySession(tokens);
        } catch {
          clearTokens();
          if (!cancelled) {
            setAccessToken(null);
            setUser(null);
          }
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
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (refreshToken) {
      try {
        await authApi.logout(refreshToken);
      } catch {
        // Still clear local session
      }
    }
    clearTokens();
    setAccessToken(null);
    setUser(null);
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
