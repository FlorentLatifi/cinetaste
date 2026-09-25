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
import {
  setEmailUnverifiedHandler,
  setSessionExpiredHandler,
  tryRefreshSession,
} from "../../api/client";
import { clearLegacyTokenStorage, setAccessToken } from "../../api/tokenStore";
import { broadcastSession, onSessionEvent } from "./sessionChannel";

type AuthState = {
  user: User | null;
  accessToken: string | null;
  loading: boolean;
  /** The API refused something because this address is not confirmed. */
  emailVerificationRequired: boolean;
  /** The first request is slow enough to be worth explaining. */
  bootstrapSlow: boolean;
  /** We stopped waiting for it. */
  bootstrapFailed: boolean;
  retryBootstrap: () => void;
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
  const [emailVerificationRequired, setEmailVerificationRequired] = useState(false);
  const [bootstrapSlow, setBootstrapSlow] = useState(false);
  const [bootstrapFailed, setBootstrapFailed] = useState(false);
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);

  const applySession = useCallback((tokens: authApi.TokenResponse) => {
    setAccessToken(tokens.access_token);
    setAccessTokenState(tokens.access_token);
    setUser(tokens.user);
    // Known at sign-in now, so "check your inbox" shows straight away rather
    // than after the first gated request comes back 403.
    setEmailVerificationRequired(
      Boolean(tokens.email_verification_required) && !tokens.user.email_verified_at,
    );
  }, []);

  const refreshUser = useCallback(async () => {
    const tokens = await tryRefreshSession();
    if (!tokens) return;
    const me = await authApi.getMe(tokens.access_token);
    setAccessTokenState(tokens.access_token);
    setUser(me);
    if (me.email_verified_at) setEmailVerificationRequired(false);
  }, []);

  // How long the first request may take before we say something, and before we
  // stop waiting. The API sleeps on its free tier and a cold start costs about
  // fifty seconds, so the explanation comes early and the deadline comes late —
  // a bare spinner for a minute reads as broken, but giving up at five seconds
  // would turn a slow start into a failure.
  const SLOW_AFTER_MS = 5_000;

  useEffect(() => {
    let cancelled = false;
    clearLegacyTokenStorage();
    setBootstrapSlow(false);
    setBootstrapFailed(false);

    const slowTimer = setTimeout(() => {
      if (!cancelled) setBootstrapSlow(true);
    }, SLOW_AFTER_MS);

    async function bootstrap() {
      try {
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
      } catch {
        // tryRefreshSession swallows its own errors, so reaching here means
        // something unexpected. Treat it as "could not tell" rather than
        // "signed out", because the difference matters to what we show.
        if (!cancelled) setBootstrapFailed(true);
      } finally {
        if (!cancelled) {
          clearTimeout(slowTimer);
          setLoading(false);
        }
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
      clearTimeout(slowTimer);
    };
  }, [applySession, bootstrapAttempt]);

  const retryBootstrap = useCallback(() => {
    setLoading(true);
    setBootstrapAttempt((n) => n + 1);
  }, []);

  // When apiFetch gets 401 and refresh fails, drop local session immediately.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setAccessToken(null);
      setAccessTokenState(null);
      setUser(null);
      setEmailVerificationRequired(false);
      clearLegacyTokenStorage();
    });
    return () => setSessionExpiredHandler(null);
  }, []);

  // A 403 the user can actually resolve, unlike the rest of them.
  useEffect(() => {
    setEmailUnverifiedHandler(() => setEmailVerificationRequired(true));
    return () => setEmailUnverifiedHandler(null);
  }, []);

  // Follow what the other tabs did. Signing out in one tab used to leave every
  // other one looking signed in until its next request happened to 401 — which
  // on a page nobody is clicking might be never.
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event === "signed-out") {
          setAccessToken(null);
          setAccessTokenState(null);
          setUser(null);
          setEmailVerificationRequired(false);
          return;
        }
        // Signed in elsewhere: fetch our own token through the shared cookie
        // rather than being handed one. Nothing sensitive crossed the channel.
        void refreshUser().catch(() => undefined);
      }),
    [refreshUser],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login({ email, password });
      applySession(tokens);
      broadcastSession("signed-in");
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
      broadcastSession("signed-in");
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
    setEmailVerificationRequired(false);
    clearLegacyTokenStorage();
    broadcastSession("signed-out");
  }, []);

  const value = useMemo(
    () => ({
      user,
      accessToken,
      loading,
      emailVerificationRequired,
      bootstrapSlow,
      bootstrapFailed,
      retryBootstrap,
      login,
      register,
      logout,
      refreshUser,
    }),
    [
      user,
      accessToken,
      loading,
      emailVerificationRequired,
      bootstrapSlow,
      bootstrapFailed,
      retryBootstrap,
      login,
      register,
      logout,
      refreshUser,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
