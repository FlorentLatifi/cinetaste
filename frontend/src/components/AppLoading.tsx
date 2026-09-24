type Props = {
  /** The first request is taking long enough that silence is worse than a sentence. */
  slow?: boolean;
  /** We gave up waiting. */
  failed?: boolean;
  onRetry?: () => void;
};

/**
 * What the app shows while it works out whether you are signed in.
 *
 * The API sleeps on its free tier and takes the better part of a minute to
 * wake. A bare spinner for that long reads as broken, so after a few seconds
 * this says what is actually happening — and the request is left running,
 * because it is about to succeed. Cancelling at five seconds would turn a slow
 * start into a failure.
 */
export function AppLoading({ slow, failed, onRetry }: Props) {
  if (failed) {
    return (
      <div className="center-screen">
        <div className="boot-card" role="alert">
          <h1 className="boot-title">Can’t reach CineTaste</h1>
          <p className="boot-message">
            The server didn’t answer. It may still be starting up — give it a
            moment and try again.
          </p>
          {onRetry && (
            <button type="button" className="btn primary" onClick={onRetry}>
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="center-screen">
      <div className="boot-card">
        <div className="spinner" aria-hidden="true" />
        <p className="boot-message" role="status" aria-live="polite">
          {slow ? "Waking the server — this takes about a minute on first visit." : "Loading"}
        </p>
      </div>
    </div>
  );
}
