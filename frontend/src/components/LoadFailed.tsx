import type { ReactNode } from "react";

type Props = {
  /** What the user was trying to see, e.g. "your watchlist". */
  what: string;
  message: string;
  onRetry: () => void;
  /** An escape hatch when retrying is not what they want. */
  children?: ReactNode;
};

/**
 * A failed load with a way out of it.
 *
 * Search, the watchlist, history and title detail all used to render the error
 * as a red line and stop there — no button, no link, nothing to do but leave.
 * Recoverable failures are the common case (a dropped connection, an API still
 * waking up), so the recovery belongs on screen next to the explanation.
 */
export function LoadFailed({ what, message, onRetry, children }: Props) {
  return (
    <div className="load-failed" role="alert">
      <h2 className="load-failed-title">Couldn’t load {what}</h2>
      <p className="load-failed-message">{message}</p>
      <div className="load-failed-actions">
        <button type="button" className="btn primary" onClick={onRetry}>
          Try again
        </button>
        {children}
      </div>
    </div>
  );
}
