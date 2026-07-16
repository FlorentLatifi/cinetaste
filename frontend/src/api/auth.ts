import { apiFetch } from "./client";

export type User = {
  id: string;
  email: string;
  display_name: string | null;
  onboarding_completed_at: string | null;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export function register(input: {
  email: string;
  password: string;
  display_name?: string;
}) {
  return apiFetch<TokenResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(input: { email: string; password: string }) {
  return apiFetch<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Uses httpOnly refresh cookie (no body). */
export function refresh() {
  return apiFetch<TokenResponse>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** Clears server session + httpOnly cookie. */
export function logout() {
  return apiFetch<void>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function getMe(accessToken: string) {
  return apiFetch<User>("/me", {}, accessToken);
}
