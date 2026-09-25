/**
 * What a guest did before signing up, kept in this browser so none of it is
 * lost: their card answers become onboarding answers, and the picks they
 * saved land in their watchlist.
 *
 * localStorage rather than sessionStorage on purpose: the email-confirmation
 * link opens in a new tab, and sessionStorage is per tab. It holds movie
 * opinions only — no identity — and expires after a week.
 */
import type { OnboardingAction, OnboardingReaction } from "../../api/titles";

const KEY = "cinetaste.guestDraft.v1";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
// Mirrors the server's bound on /onboarding/complete.
const MAX_REACTIONS = 80;
const MAX_SAVED = 50;

const ACTIONS: ReadonlySet<string> = new Set<OnboardingAction>([
  "haven't_seen",
  "not_interested",
  "mid",
  "rate_1",
  "rate_2",
  "rate_3",
  "rate_4",
]);

export type GuestDraft = {
  reactions: OnboardingReaction[];
  /** Title ids the guest wanted to watch. */
  saved: string[];
  savedAt: number;
};

const EMPTY: GuestDraft = { reactions: [], saved: [], savedAt: 0 };

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    // Private mode, blocked site data: the draft is a convenience, never a
    // requirement, so everything below degrades to "nothing saved".
    return null;
  }
}

function isReaction(value: unknown): value is OnboardingReaction {
  if (!value || typeof value !== "object") return false;
  const r = value as Record<string, unknown>;
  return typeof r.title_id === "string" && typeof r.action === "string" && ACTIONS.has(r.action);
}

export function loadGuestDraft(now = Date.now()): GuestDraft {
  const store = storage();
  if (!store) return EMPTY;
  try {
    const raw = store.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<GuestDraft>;
    const savedAt = typeof parsed.savedAt === "number" ? parsed.savedAt : 0;
    if (now - savedAt > MAX_AGE_MS) {
      store.removeItem(KEY);
      return EMPTY;
    }
    return {
      reactions: (Array.isArray(parsed.reactions) ? parsed.reactions : [])
        .filter(isReaction)
        .slice(-MAX_REACTIONS),
      saved: (Array.isArray(parsed.saved) ? parsed.saved : [])
        .filter((id): id is string => typeof id === "string")
        .slice(-MAX_SAVED),
      savedAt,
    };
  } catch {
    return EMPTY;
  }
}

export function saveGuestDraft(
  draft: Pick<GuestDraft, "reactions" | "saved">,
  now = Date.now(),
): void {
  const store = storage();
  if (!store) return;
  // One answer per title, the latest; the server applies the same rule.
  const byTitle = new Map<string, OnboardingReaction>();
  for (const r of draft.reactions) {
    byTitle.delete(r.title_id);
    byTitle.set(r.title_id, r);
  }
  const value: GuestDraft = {
    reactions: [...byTitle.values()].slice(-MAX_REACTIONS),
    saved: [...new Set(draft.saved)].slice(-MAX_SAVED),
    savedAt: now,
  };
  try {
    store.setItem(KEY, JSON.stringify(value));
  } catch {
    // Quota or blocked storage: losing the draft is acceptable.
  }
}

export function clearGuestDraft(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // Nothing to do.
  }
}

export function hasGuestDraft(draft: GuestDraft = loadGuestDraft()): boolean {
  return draft.reactions.length > 0 || draft.saved.length > 0;
}
