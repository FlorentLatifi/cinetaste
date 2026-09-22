/**
 * The rating scale, defined once.
 *
 * For You, the title detail page, onboarding and the undo toast all used
 * different words for the same button ("Good" / "I like it so much" /
 * "Rated I like it so much"), which made the product feel inconsistent and
 * the confirmation toast look like it recorded something else.
 *
 * Event names are the API contract (see backend app/domain/taste_signals.py);
 * only the wording lives here.
 */
import type { FeedbackAction } from "../../components/ActionToast";

export type RatingEvent =
  | "rate_4"
  | "rate_3"
  | "rate_2"
  | "mid"
  | "rate_1"
  | "haven't_seen";

export type RatingOption = {
  event: RatingEvent;
  /** Button text. */
  label: string;
  /** Sentence used to confirm the action ("Loved it · Arrival"). */
  done: string;
  /** Number key on For You. */
  key: string;
  /** Letter shortcut on For You. */
  shortcut: string;
  hint: string;
  emoji: string;
  className: string;
};

/** Best first: the order shown on For You, keys 1–6. */
export const RATING_SCALE: readonly RatingOption[] = [
  {
    event: "rate_4",
    label: "Loved it",
    done: "Loved it",
    key: "1",
    shortcut: "l",
    hint: "One of my favourites",
    emoji: "✦",
    className: "ob-rate-fav",
  },
  {
    event: "rate_3",
    label: "Really liked it",
    done: "Really liked it",
    key: "2",
    shortcut: "r",
    hint: "I'd recommend it",
    emoji: "👍",
    className: "ob-rate-good",
  },
  {
    event: "rate_2",
    label: "Liked it",
    done: "Liked it",
    key: "3",
    shortcut: "i",
    hint: "Enjoyed it",
    emoji: "🙂",
    className: "ob-rate-like",
  },
  {
    event: "mid",
    label: "It was ok",
    done: "Rated it ok",
    key: "4",
    shortcut: "o",
    hint: "Fine, not special",
    emoji: "😐",
    className: "ob-rate-ok",
  },
  {
    event: "haven't_seen",
    label: "Haven't seen it",
    done: "Marked as not seen",
    key: "5",
    shortcut: "h",
    hint: "Doesn't count as a rating",
    emoji: "❔",
    className: "ob-rate-unseen",
  },
  {
    event: "rate_1",
    label: "Didn't like it",
    done: "Didn't like it",
    key: "6",
    shortcut: "d",
    hint: "Not for me",
    emoji: "👎",
    className: "ob-rate-bad",
  },
] as const;

/** Onboarding asks only about titles the user has seen, worst → best. */
export const ONBOARDING_RATINGS: readonly RatingOption[] = [
  "rate_1",
  "mid",
  "rate_2",
  "rate_3",
  "rate_4",
].map((event) => RATING_SCALE.find((o) => o.event === event)!);

/** The detail page's "how was it?" strip, worst → best. */
export const POST_WATCH_RATINGS = ONBOARDING_RATINGS;

export const RATING_DONE_LABELS = Object.fromEntries(
  RATING_SCALE.map((o) => [o.event, o.done]),
) as Record<RatingEvent, string>;

export function ratingLabel(event: FeedbackAction): string | undefined {
  return RATING_SCALE.find((o) => o.event === event)?.label;
}
