/**
 * Where to send someone after they sign in.
 *
 * A signed-out visitor who opens a protected link — a title someone shared, a
 * bookmark of their watchlist — is sent to /login. Without remembering where
 * they were going, signing in dropped them on the home page and the link was
 * lost. The destination travels in router state, never in the URL, and is only
 * honoured when it is a path inside this app: anything else (another origin,
 * a protocol-relative "//evil.example", a backslash trick) is an open redirect.
 */

const AUTH_PAGES = ["/login", "/register", "/forgot-password", "/reset-password"];

export type ReturnState = { from?: unknown } | null | undefined;

export function returnPath(state: unknown): string | null {
  const from = (state as ReturnState)?.from;
  if (typeof from !== "string" || from.length === 0 || from.length > 2048) return null;
  // Same-app paths only: "/x" yes; "//host", "/\\host" and absolute URLs no.
  if (!from.startsWith("/") || from.startsWith("//") || from.startsWith("/\\")) return null;
  if (/[\u0000-\u001f]/.test(from)) return null;
  const path = from.split(/[?#]/, 1)[0];
  // Going back to a sign-in page after signing in would loop.
  if (AUTH_PAGES.some((page) => path === page || path.startsWith(`${page}/`))) return null;
  return from;
}

/** Router state that carries the current location to the sign-in page. */
export function returnState(pathname: string, search = "", hash = ""): { from: string } {
  return { from: `${pathname}${search}${hash}` };
}
