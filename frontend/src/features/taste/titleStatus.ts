/**
 * What a viewer has done with one title, and what each action changes.
 *
 * Mirrors `resolve_title_state` in backend app/domain/taste_signals.py — the
 * rule the API uses to store `user_title_state` and report `my_state` /
 * `my_rating`:
 *
 *   - `clear` resets everything before it;
 *   - the strongest tier wins (opinion > intent > none), the latest within it;
 *   - so "Want to watch" or "Seen it — skip rating" never replaces a rating,
 *     and "Haven't seen it" never replaces anything.
 *
 * Kept free of React so the rule is unit-tested in one place
 * (titleStatus.test.ts) and the page only decides what to render.
 */
import type { FeedbackAction } from "../../components/ActionToast";

export type ViewerStatus = { state: string | null; rating: string | null };
export const NO_STATUS: ViewerStatus = { state: null, rating: null };

type Tier = 0 | 1 | 2 | 3; // none < (implicit) < intent < opinion

const OPINION_STATES = new Set(["like", "dislike", "rated", "not_interested"]);
const INTENT_STATES = new Set(["watchlist", "watched"]);

const OPINION_EVENTS = new Set<FeedbackAction>([
  "like",
  "dislike",
  "not_interested",
  "watched_liked",
  "watched_disliked",
  "mid",
  "rate_1",
  "rate_2",
  "rate_3",
  "rate_4",
]);
const INTENT_EVENTS = new Set<FeedbackAction>(["watchlist", "watched"]);
const RATING_EVENTS = new Set<FeedbackAction>(["mid", "rate_1", "rate_2", "rate_3", "rate_4"]);

const STATE_FROM_EVENT: Record<FeedbackAction, string | null> = {
  like: "like",
  dislike: "dislike",
  watchlist: "watchlist",
  not_interested: "not_interested",
  watched: "watched",
  watched_liked: "like",
  watched_disliked: "dislike",
  mid: "rated",
  "haven't_seen": null, // the API reports it as no state
  rate_1: "dislike",
  rate_2: "rated",
  rate_3: "like",
  rate_4: "like",
};

function eventTier(event: FeedbackAction): Tier {
  if (OPINION_EVENTS.has(event)) return 3;
  if (INTENT_EVENTS.has(event)) return 2;
  return 0;
}

function statusTier(status: ViewerStatus): Tier {
  if (status.rating || (status.state && OPINION_STATES.has(status.state))) return 3;
  if (status.state && INTENT_STATES.has(status.state)) return 2;
  return 0;
}

/** The viewer has judged the title (a rating, like/dislike, or not interested). */
export function hasOpinion(status: ViewerStatus): boolean {
  return statusTier(status) === 3;
}

/** The viewer has rated the title — the thing no toggle may ever erase. */
export function hasRating(status: ViewerStatus): boolean {
  if (status.rating) return true;
  return status.state === "like" || status.state === "dislike" || status.state === "rated";
}

/** What the API will report after `event`, given what it reports now. */
export function statusAfter(before: ViewerStatus, event: FeedbackAction | "clear"): ViewerStatus {
  if (event === "clear") return NO_STATUS;
  const tier = eventTier(event);
  // A weaker action is recorded but does not change what the title "is".
  if (tier === 0 || tier < statusTier(before)) return before;
  return {
    state: STATE_FROM_EVENT[event],
    rating: RATING_EVENTS.has(event) ? event : null,
  };
}

/** The single event that recreates a status, or null for "nothing". */
export function eventFor(status: ViewerStatus): FeedbackAction | null {
  if (status.rating) return status.rating as FeedbackAction;
  switch (status.state) {
    case "watchlist":
    case "not_interested":
    case "watched":
    case "like":
    case "dislike":
      return status.state;
    default:
      return null;
  }
}

/**
 * Requests that turn `current` back into `before`, in order.
 *
 * Re-sending the old event is enough whenever it is at least as strong as
 * what is there now (rating → other rating, watchlist → watched): one request,
 * nothing erased. `clear` is needed only to step *down* a tier — e.g. undoing
 * a rating given to a watchlisted title — and then `before` holds no rating,
 * so a clear can never erase one.
 */
export function undoRequests(
  before: ViewerStatus,
  current: ViewerStatus,
): Array<FeedbackAction | "clear"> {
  const restore = eventFor(before);
  if (restore && eventTier(restore) >= statusTier(current)) return [restore];
  return restore ? ["clear", restore] : ["clear"];
}

/** "Want to watch" belongs to titles the viewer has not seen or judged. */
export function offersWatchlist(status: ViewerStatus): boolean {
  return !hasRating(status) && (status.state === null || status.state === "watchlist");
}

/** "Not for me" is offered until the viewer has seen or rated the title. */
export function offersNotForMe(status: ViewerStatus): boolean {
  return (
    !hasRating(status) &&
    (status.state === null || status.state === "watchlist" || status.state === "not_interested")
  );
}

/** "Seen it — skip rating" only changes anything for an unseen, unjudged title. */
export function offersSkipRating(status: ViewerStatus): boolean {
  return !hasRating(status) && (status.state === null || status.state === "watchlist");
}

/**
 * Un-toggling "In your watchlist" / "Not for you" sends `clear`, which drops
 * every earlier event for the title. Only safe while nothing worth keeping is
 * underneath — never when the title has a rating.
 */
export function canClearFromToggle(status: ViewerStatus): boolean {
  return !hasRating(status);
}
