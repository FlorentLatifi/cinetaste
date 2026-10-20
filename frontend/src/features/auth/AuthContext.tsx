import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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
import { hasSessionHint, setSessionHint } from "./sessionHint";

/**
 * Why the last session ended, so the signed-in pages can send people somewhere
 * that fits: a confirmation after deleting the account, the home page after
 * signing out, the sign-in form when a session simply expired.
 */
export type SignOutReason = "signed_out" | "deleted" | "expired";

export type RegisterResult =
  | { status: "signed_in" }
  | { status: "confirmation_sent"; email: string; devToken: string | null };

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
  /** Signs in straight away, or reports that a confirmation link was mailed. */
  register: (email: string, password: string, displayName?: string) => Promise<RegisterResult>;
  confirmRegistration: (token: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /**
   * Forget the session here and in other tabs without calling the API. For
   * when the server has already ended it (account deletion clears the cookie).
   */
  endSession: (reason: SignOutReason) => void;
  /** Why the last session ended, or null while signed in / never signed in. */
  signOutReason: SignOutReason | null;
  refreshUser: () => Promise<void>;
  /**
   * True when this browser was signed in last time, so pages should wait for
   * the session check. False for everyone else, who can be shown the public
   * page while the check runs (see sessionHint).
   */
  sessionLikely: boolean;
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
  const [sessionLikely, setSessionLikely] = useState(hasSessionHint);
  const [signOutReason, setSignOutReason] = useState<SignOutReason | null>(null);
  // Bumped by every sign-in and sign-out here. The page-load session check
  // compares it before applying its answer: a first-time visitor can sign in
  // while that check is still waiting on a sleeping API, and its late "no
  // session" must not undo the sign-in.
  const sessionEpoch = useRef(0);

  const forgetSession = useCallback(() => {
    sessionEpoch.current += 1;
    setSessionHint(false);
    setSessionLikely(false);
  }, []);

  const applySession = useCallback((tokens: authApi.TokenResponse) => {
    sessionEpoch.current += 1;
    // A sign-in answers what the session check was asking, so stop waiting.
    setLoading(false);
    setSessionHint(true);
    setSessionLikely(true);
    setSignOutReason(null);
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
      const epoch = sessionEpoch.current;
      try {
        // Goes through the shared single-flight refresh: StrictMode's double
        // effect and other tabs reuse one request instead of racing it.
        const tokens = await tryRefreshSession();
        if (cancelled) return;
        // Signed in or out while this was in flight: that answer is newer.
        if (sessionEpoch.current !== epoch) return;
        if (tokens) {
          applySession(tokens);
        } else {
          setAccessToken(null);
          setAccessTokenState(null);
          setUser(null);
          forgetSession();
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
  }, [applySession, forgetSession, bootstrapAttempt]);

  const retryBootstrap = useCallback(() => {
    setLoading(true);
    setBootstrapAttempt((n) => n + 1);
  }, []);

  // When apiFetch gets 401 and refresh fails, drop local session immediately.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setSignOutReason("expired");
      setAccessToken(null);
      setAccessTokenState(null);
      setUser(null);
      setEmailVerificationRequired(false);
      forgetSession();
      clearLegacyTokenStorage();
    });
    return () => setSessionExpiredHandler(null);
  }, [forgetSession]);

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
          setSignOutReason("signed_out");
          setAccessToken(null);
          setAccessTokenState(null);
          setUser(null);
          setEmailVerificationRequired(false);
          forgetSession();
          return;
        }
        // Signed in elsewhere: fetch our own token through the shared cookie
        // rather than being handed one. Nothing sensitive crossed the channel.
        void refreshUser().catch(() => undefined);
      }),
    [refreshUser, forgetSession],
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
    async (email: string, password: string, displayName?: string): Promise<RegisterResult> => {
      const res = await authApi.register({
        email,
        password,
        display_name: displayName,
      });
      if (!("access_token" in res)) {
        // Verify-first: no account exists until the mailed link is used.
        return { status: "confirmation_sent", email: res.email, devToken: res.dev_confirmation_token };
      }
      applySession(res);
      broadcastSession("signed-in");
      return { status: "signed_in" };
    },
    [applySession],
  );

  const confirmRegistration = useCallback(
    async (token: string, password: string) => {
      const tokens = await authApi.confirmRegistration(token, password);
      applySession(tokens);
      broadcastSession("signed-in");
    },
    [applySession],
  );

  const endSession = useCallback(
    (reason: SignOutReason) => {
      // Set together with the user so the guard sees both in one render.
      setSignOutReason(reason);
      setAccessToken(null);
      setAccessTokenState(null);
      setUser(null);
      setEmailVerificationRequired(false);
      forgetSession();
      clearLegacyTokenStorage();
      broadcastSession("signed-out");
    },
    [forgetSession],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Still clear local session
    }
    endSession("signed_out");
  }, [endSession]);

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
      confirmRegistration,
      logout,
      endSession,
      signOutReason,
      refreshUser,
      sessionLikely,
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
      confirmRegistration,
      logout,
      endSession,
      signOutReason,
      refreshUser,
      sessionLikely,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
