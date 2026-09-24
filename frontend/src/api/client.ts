import * as authApi from "./auth";
import { captureFrontendError } from "../observability";
import { clearLegacyTokenStorage, getAccessToken, setAccessToken } from "./tokenStore";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

/**
 * A request with no deadline can hang forever, and `fetch` has none by
 * default. Sixty seconds is deliberately generous: the API sleeps on its free
 * tier and a cold start costs about fifty, so a tighter bound would abort
 * requests that were seconds from succeeding. This is the backstop for a
 * connection that has actually died, not a latency budget.
 */
const DEFAULT_TIMEOUT_MS = 60_000;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "ApiError";
  }

  /** No response at all — a timeout or a dead connection, not a rejection. */
  get isTransport(): boolean {
    return this.status === 0;
  }
}

/** Called when access token is invalid and refresh fails (session dead). */
type SessionExpiredHandler = () => void;
let sessionExpiredHandler: SessionExpiredHandler | null = null;

export function setSessionExpiredHandler(handler: SessionExpiredHandler | null) {
  sessionExpiredHandler = handler;
}

function notifySessionExpired() {
  sessionExpiredHandler?.();
}

/**
 * Called when the server requires a confirmed email address and this one is
 * not. Only fires where REQUIRE_EMAIL_VERIFICATION is on. Without it the
 * user would get a bare error on a page they cannot fix anything from.
 */
type EmailUnverifiedHandler = () => void;
let emailUnverifiedHandler: EmailUnverifiedHandler | null = null;

export function setEmailUnverifiedHandler(handler: EmailUnverifiedHandler | null) {
  emailUnverifiedHandler = handler;
}

/**
 * Single-flight refresh: concurrent 401s *and* session restore on load share
 * one /auth/refresh call. Two calls with the same cookie would look like token
 * reuse to the server, which revokes the whole session family.
 */
let refreshInFlight: Promise<authApi.TokenResponse | null> | null = null;

export async function tryRefreshSession(): Promise<authApi.TokenResponse | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      // Cookie sent automatically with credentials: "include"
      const tokens = await authApi.refresh();
      setAccessToken(tokens.access_token);
      return tokens;
    } catch {
      setAccessToken(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

export async function tryRefreshAccessToken(): Promise<string | null> {
  return (await tryRefreshSession())?.access_token ?? null;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  accessToken?: string | null,
  _retried = false,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = accessToken ?? getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const timeout = new AbortController();
  const deadline = setTimeout(() => timeout.abort(), DEFAULT_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      credentials: "include",
      signal: options.signal ?? timeout.signal,
    });
  } catch (cause) {
    // Abort and connection failure land here alike, and both mean the same
    // thing to the person waiting: it did not arrive.
    const timedOut = timeout.signal.aborted;
    throw new ApiError(
      0,
      timedOut ? "timeout" : "network_error",
      timedOut
        ? "The server took too long to answer."
        : "Could not reach the server. Check your connection.",
      { cause },
    );
  } finally {
    clearTimeout(deadline);
  }

  // Attempt one silent refresh on expired access token (skip auth endpoints
  // except we allow refresh itself only once).
  if (response.status === 401 && !_retried && !path.startsWith("/auth/")) {
    const next = await tryRefreshAccessToken();
    if (next) {
      return apiFetch<T>(path, options, next, true);
    }
    // Refresh failed — clear React session so Protected routes → login
    notifySessionExpired();
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    if (response.status === 403 && data.code === "email_not_verified") {
      emailUnverifiedHandler?.();
    }
    const err = new ApiError(
      response.status,
      typeof data.code === "string" ? data.code : "error",
      typeof data.message === "string" ? data.message : "Request failed",
    );
    // Report server failures (not 401/404 noise)
    if (response.status >= 500) {
      captureFrontendError(err, { path, status: response.status, code: err.code });
    }
    throw err;
  }

  return data as T;
}

// Wipe legacy localStorage tokens once per load
clearLegacyTokenStorage();
