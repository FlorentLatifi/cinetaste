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

export function forgotPassword(email: string) {
  return apiFetch<{ message: string; dev_reset_token?: string | null }>(
    "/auth/forgot-password",
    {
      method: "POST",
      body: JSON.stringify({ email }),
    },
  );
}

export function resetPassword(token: string, new_password: string) {
  return apiFetch<void>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  });
}

export function deleteAccount(accessToken: string, password: string) {
  return apiFetch<void>(
    "/me",
    {
      method: "DELETE",
      body: JSON.stringify({ password, confirm: "DELETE" }),
    },
    accessToken,
  );
}

export type TasteFeature = {
  key: string;
  family: string;
  label: string;
  weight: number;
};

export type TasteSummary = {
  version: number;
  updated_at: string | null;
  has_vector: boolean;
  feature_count: number;
  anchor_count: number;
  likes: TasteFeature[];
  dislikes: TasteFeature[];
  ready: boolean;
};

export function getTasteSummary(accessToken: string) {
  return apiFetch<TasteSummary>("/me/taste", {}, accessToken);
}
