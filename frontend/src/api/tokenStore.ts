/**
 * In-memory access token only.
 * Refresh lives in an httpOnly cookie set by the API — never localStorage.
 */

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** Remove legacy keys from older clients. */
export function clearLegacyTokenStorage(): void {
  try {
    localStorage.removeItem("ct_access");
    localStorage.removeItem("ct_refresh");
  } catch {
    // ignore private mode / blocked storage
  }
}
