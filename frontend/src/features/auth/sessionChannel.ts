/**
 * Tells the app's other tabs that the session changed.
 *
 * Signing out in one tab used to leave every other tab looking signed in until
 * its next request happened to come back 401 — which, on a page the user is
 * only reading, might be never.
 *
 * What crosses between tabs is a timestamp, never a token. A tab told that
 * someone signed in goes and gets its own access token through the httpOnly
 * refresh cookie, exactly as it would on a cold load. Putting the token in
 * localStorage would undo the reason it lives in memory in the first place.
 *
 * The same `storage` event already keeps the theme and contrast settings in
 * step; this is that mechanism applied to the thing that matters more.
 */

const KEY = "ct_session_event";

export type SessionEvent = "signed-in" | "signed-out";

/** Announce to other tabs. The tab that calls this is not notified. */
export function broadcastSession(event: SessionEvent): void {
  try {
    // The timestamp is what makes each write distinct — `storage` does not
    // fire when the value is unchanged, so two sign-outs in a row would be
    // silent without it.
    localStorage.setItem(KEY, `${event}:${Date.now()}`);
  } catch {
    // Private mode, blocked storage. Tabs simply stay independent.
  }
}

/** Listen for session changes made in other tabs. Returns an unsubscribe. */
export function onSessionEvent(handler: (event: SessionEvent) => void): () => void {
  if (typeof window === "undefined") return () => undefined;

  const listener = (e: StorageEvent) => {
    if (e.key !== KEY || !e.newValue) return;
    const event = e.newValue.split(":")[0];
    if (event === "signed-in" || event === "signed-out") handler(event);
  };

  window.addEventListener("storage", listener);
  return () => window.removeEventListener("storage", listener);
}
