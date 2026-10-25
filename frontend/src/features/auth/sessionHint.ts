/**
 * Whether this browser was signed in last time. A hint, never a credential:
 * the session itself is the httpOnly refresh cookie, which scripts can't read.
 *
 * It exists so a first-time visitor sees the landing page immediately. Without
 * it every page load first asked the API "am I signed in?", and on a free
 * instance that has gone to sleep that question takes ~50 seconds — so a new
 * visitor's first impression was a spinner. Now only people who were signed
 * in wait for the answer; everyone else gets the page at once while the check
 * finishes in the background.
 */
const KEY = "ct_signed_in";

export function hasSessionHint(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function setSessionHint(signedIn: boolean): void {
  try {
    if (signedIn) window.localStorage.setItem(KEY, "1");
    else window.localStorage.removeItem(KEY);
  } catch {
    // Blocked storage: everyone is treated as a first-time visitor, which only
    // costs a brief landing page before For You.
  }
}
