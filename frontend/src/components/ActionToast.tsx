export type FeedbackAction =
  | "like"
  | "dislike"
  | "watchlist"
  | "not_interested"
  | "watched"
  | "watched_liked"
  | "watched_disliked"
  | "rate_1"
  | "rate_2"
  | "rate_3"
  | "rate_4";

export const FEEDBACK_ACTION_LABELS: Record<FeedbackAction, string> = {
  like: "Liked",
  dislike: "Passed",
  watchlist: "Saved to watchlist",
  not_interested: "Marked not interested",
  watched: "Marked as watched",
  watched_liked: "Watched and liked",
  watched_disliked: "Watched and disliked",
  rate_1: "Rated Bad",
  rate_2: "Rated It's ok",
  rate_3: "Rated Good",
  rate_4: "Rated Favorite",
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
