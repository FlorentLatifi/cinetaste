export type FeedbackAction =
  | "like"
  | "dislike"
  | "watchlist"
  | "not_interested";

export const FEEDBACK_ACTION_LABELS: Record<FeedbackAction, string> = {
  like: "Liked",
  dislike: "Passed",
  watchlist: "Saved to watchlist",
  not_interested: "Marked not interested",
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
