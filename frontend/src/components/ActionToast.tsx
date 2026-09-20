import { RATING_DONE_LABELS } from "../features/taste/ratingScale";

export type FeedbackAction =
  | "like"
  | "dislike"
  | "watchlist"
  | "not_interested"
  | "watched"
  | "watched_liked"
  | "watched_disliked"
  | "mid"
  | "haven't_seen"
  | "rate_1"
  | "rate_2"
  | "rate_3"
  | "rate_4";

/**
 * What the undo toast says after each action. Ratings reuse the wording of the
 * button the user pressed (features/taste/ratingScale), so the confirmation
 * never sounds like a different rating was recorded.
 */
export const FEEDBACK_ACTION_LABELS: Record<FeedbackAction, string> = {
  like: "Liked",
  dislike: "Passed",
  watchlist: "Saved to watchlist",
  not_interested: "Marked not interested",
  watched: "Marked as watched",
  watched_liked: "Watched and liked",
  watched_disliked: "Watched and disliked",
  ...RATING_DONE_LABELS,
};

export const ACTION_TOAST_MS = 8_000;

type Props = {
  message: string;
  undoBusy?: boolean;
  onUndo: () => void;
  onDismiss: () => void;
};

/** Fixed bottom toast with Undo — used on For You and title detail. */
export function ActionToast({
  message,
  undoBusy = false,
  onUndo,
  onDismiss,
}: Props) {
  return (
    <div className="feed-toast" role="status" aria-live="polite">
      <p className="feed-toast-msg">{message}</p>
      <button
        type="button"
        className="btn ghost feed-toast-undo"
        disabled={undoBusy}
        onClick={onUndo}
      >
        {undoBusy ? "Undoing…" : "Undo"}
      </button>
      <button
        type="button"
        className="btn ghost feed-toast-dismiss"
        aria-label="Dismiss notification"
        onClick={onDismiss}
      >
        ✕
      </button>
    </div>
  );
}
