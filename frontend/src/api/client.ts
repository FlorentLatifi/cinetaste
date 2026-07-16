import * as authApi from "./auth";
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
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new ApiError(
      response.status,
      typeof data.code === "string" ? data.code : "error",
      typeof data.message === "string" ? data.message : "Request failed",
    );
  }

  return data as T;
}

// Wipe legacy localStorage tokens once per load
clearLegacyTokenStorage();
