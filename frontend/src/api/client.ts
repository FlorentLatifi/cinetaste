import * as authApi from "./auth";
import { captureFrontendError } from "../observability";
import { clearLegacyTokenStorage, getAccessToken, setAccessToken } from "./tokenStore";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
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

/** Single-flight refresh so concurrent 401s share one /auth/refresh call. */
let refreshInFlight: Promise<string | null> | null = null;

export async function tryRefreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      // Cookie sent automatically with credentials: "include"
      const tokens = await authApi.refresh();
      setAccessToken(tokens.access_token);
      return tokens.access_token;
    } catch {
      setAccessToken(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
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

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

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
