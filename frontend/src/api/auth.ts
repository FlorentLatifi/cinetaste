import { apiFetch } from "./client";

export type User = {
  id: string;
  email: string;
  display_name: string | null;
  /** null until the address is confirmed. */
  email_verified_at: string | null;
  onboarding_completed_at: string | null;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  user: User;
  /** The server gates the product on a confirmed address and this one isn't. */
  email_verification_required?: boolean;
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

/** Consume a link from the verification email. No session required. */
export function verifyEmail(token: string) {
  return apiFetch<User>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

/**
 * Ask for a fresh verification link for the signed-in account.
 * Authenticated on purpose: an endpoint that took an email would mail anyone
 * on request and confirm which addresses are registered.
 */
export function resendVerification(accessToken: string) {
  return apiFetch<{ message: string; dev_verification_token?: string | null }>(
    "/auth/resend-verification",
    { method: "POST", body: JSON.stringify({}) },
    accessToken,
  );
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
  has_import_overlay?: boolean;
  import_overlay_count?: number;
  likes: TasteFeature[];
  dislikes: TasteFeature[];
  ready: boolean;
};

export function getTasteSummary(accessToken: string) {
  return apiFetch<TasteSummary>("/me/taste", {}, accessToken);
}

export type TasteExport = {
  schema: string;
  exported_at: string;
  profile_version: number;
  updated_at: string | null;
  has_vector: boolean;
  feature_count: number;
  anchor_count: number;
  likes: TasteFeature[];
  dislikes: TasteFeature[];
  anchors: { name: string; year?: number | null }[];
  text: string;
};

export function exportTaste(accessToken: string) {
  return apiFetch<TasteExport>("/me/taste/export", {}, accessToken);
}

export type TasteImportResult = {
  merged_features: number;
  profile_version: number;
  summary: TasteSummary;
};

export function importTaste(
  accessToken: string,
  body: {
    schema: string;
    likes: TasteFeature[];
    dislikes: TasteFeature[];
  },
) {
  return apiFetch<TasteImportResult>(
    "/me/taste/import",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
    accessToken,
  );
}

export function clearTasteImport(accessToken: string) {
  return apiFetch<TasteSummary>(
    "/me/taste/import",
    { method: "DELETE" },
    accessToken,
  );
}
