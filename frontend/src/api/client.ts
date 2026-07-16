import * as authApi from "./auth";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

const ACCESS_KEY = "ct_access";
const REFRESH_KEY = "ct_refresh";

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

async function tryRefreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (!refreshToken) return null;
    try {
      const tokens = await authApi.refresh(refreshToken);
      localStorage.setItem(ACCESS_KEY, tokens.access_token);
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
      return tokens.access_token;
    } catch {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
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
  const token = accessToken ?? localStorage.getItem(ACCESS_KEY);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  // Attempt one silent refresh on expired access token (skip auth endpoints).
  if (
    response.status === 401 &&
    !_retried &&
    !path.startsWith("/auth/") &&
    localStorage.getItem(REFRESH_KEY)
  ) {
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
