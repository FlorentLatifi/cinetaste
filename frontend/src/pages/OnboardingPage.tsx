import { useEffect, useRef } from "react";
import { RATE_OPTIONS } from "../features/onboarding/constants";
import { useOnboardingDeck } from "../features/onboarding/useOnboardingDeck";
import { heroPosterUrl, posterSrcSet, yearOf } from "../lib/poster";

export function OnboardingPage() {
  const {
    current,
    reactions,
    error,
    loading,
    loadingMore,
    submitting,
    rateMode,
    setRateMode,
    cardAnimKey,
    exiting,
    ratedCount,
    positiveCount,
    unseenCount,
    canFinish,
    progressPct,
    applyAction,
    finish,
    minRatings,
  } = useOnboardingDeck();

  const rateFirstRef = useRef<HTMLButtonElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const poster = current ? heroPosterUrl(current) : null;
  const posterSet = current ? posterSrcSet(current) : null;

  useEffect(() => {
    if (current && !loading && !submitting) {
      requestAnimationFrame(() => titleRef.current?.focus());
    }
  }, [current?.id, cardAnimKey, loading, submitting]);

  useEffect(() => {
    if (rateMode) {
      requestAnimationFrame(() => rateFirstRef.current?.focus());
    }
  }, [rateMode]);

  if (loading) {
    return (
      <div className="ob-stage ob-stage-loading">
        <div className="spinner" />
        <p className="ob-loading-copy">Curating your taste deck…</p>
      </div>
    );
  }

  if (submitting) {
    return (
      <div className="ob-stage ob-stage-loading">
        <div className="spinner" />
        <p className="eyebrow">Almost there</p>
        <h1 className="ob-loading-title">Building your taste profile</h1>
        <p className="ob-loading-copy">
          Weaving your ratings into a personal For You slate…
        </p>
      </div>
    );
  }

  if (!current && !loadingMore) {
    return (
      <div className="ob-stage ob-stage-empty">
        <p className="eyebrow">Onboarding</p>
        <h1>No titles available</h1>
        <p className="lede">
          {error || "Seed the catalog first so we can show movies to rate."}
        </p>
        {reactions.length > 0 && canFinish && (
          <button
            type="button"
            className="btn primary"
            onClick={() => void finish(reactions)}
          >
            Finish with {ratedCount} ratings
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="ob-stage">
      {poster && (
        <div
          className="ob-ambient"
          style={{ backgroundImage: `url(${poster})` }}
          aria-hidden
        />
      )}

      <header className="ob-header">
        <div className="ob-header-text">
          <p className="eyebrow">Taste calibration</p>
          <h1>Rate what you know</h1>
          <p className="ob-sub">
            Skip the unfamiliar — zero signal. Rate the ones you&apos;ve seen so
            recommendations feel like you.
          </p>
        </div>

        <div className="ob-progress" aria-live="polite" aria-atomic="true">
          <div className="ob-progress-top">
            <span className="ob-progress-count">
              <strong>{ratedCount}</strong>
              <span className="ob-progress-of"> of {minRatings} rated</span>
            </span>
            {positiveCount > 0 && (
              <span className="ob-progress-meta">
                {positiveCount} positive
                {unseenCount > 0 ? ` · ${unseenCount} skipped` : ""}
              </span>
            )}
          </div>
          <div
            className="ob-progress-bar"
            role="progressbar"
            aria-valuenow={ratedCount}
            aria-valuemin={0}
            aria-valuemax={minRatings}
            aria-label="Onboarding rating progress"
          >
            <div
              className="ob-progress-fill"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="ob-progress-hint">
            {ratedCount < minRatings
              ? `${minRatings - ratedCount} more rating${
                  minRatings - ratedCount === 1 ? "" : "s"
                } to unlock For You`
              : positiveCount < 2
                ? "Add a couple of OK / Good / Favorite picks"
                : "You can finish anytime — or keep refining"}
          </p>
        </div>
      </header>

      {current && (
        <article
          key={`${current.id}-${cardAnimKey}`}
          className={`ob-card ${exiting ? "ob-card-exit" : "ob-card-enter"}`}
          aria-labelledby="ob-current-title"
        >
          <div className="ob-poster-wrap" aria-hidden="true">
            {poster ? (
              <img
                className="ob-poster"
                src={poster}
                srcSet={posterSet ?? undefined}
                sizes={posterSet ? "(max-width: 720px) 90vw, 420px" : undefined}
                alt=""
                draggable={false}
                decoding="async"
                fetchPriority="high"
              />
            ) : (
              <div className="ob-poster ob-poster-fallback">
                <span>{current.name}</span>
              </div>
            )}
            <div className="ob-poster-fade" />
          </div>

          <div className="ob-body">
            <div className="ob-title-block">
              <h2
                ref={titleRef}
                id="ob-current-title"
                className="ob-title"
                tabIndex={-1}
              >
                {current.name}
              </h2>
              <p className="ob-meta">
                {yearOf(current) && <span>{yearOf(current)}</span>}
                {current.media_type && (
                  <span className="ob-pill">{current.media_type}</span>
                )}
                {current.vote_average > 0 && (
                  <span className="ob-score">
                    <span className="sr-only">Rating </span>★{" "}
                    {current.vote_average.toFixed(1)}
                  </span>
                )}
              </p>
              {current.genres.length > 0 && (
                <p className="ob-genres">
                  {current.genres
                    .slice(0, 4)
                    .map((g) => g.name)
                    .join(" · ")}
                </p>
              )}
              {current.overview && (
                <p className="ob-overview">{current.overview}</p>
              )}
            </div>

            {!rateMode ? (
              <div
                className="ob-actions"
                role="group"
                aria-label={`Actions for ${current.name}`}
              >
                <button
                  type="button"
                  className="ob-btn ob-btn-ghost"
                  disabled={submitting || loadingMore || exiting}
                  aria-label={`Haven't seen ${current.name} — zero taste signal`}
                  onClick={() => void applyAction("haven't_seen")}
                >
                  <span className="ob-btn-label">Haven&apos;t seen it</span>
                  <span className="ob-btn-hint">Zero signal</span>
                </button>
                <button
                  type="button"
                  className="ob-btn ob-btn-soft-danger"
                  disabled={submitting || loadingMore || exiting}
                  aria-label={`Not interested in ${current.name}`}
                  onClick={() => void applyAction("not_interested")}
                >
                  <span className="ob-btn-label">Not interested</span>
                  <span className="ob-btn-hint">Mild avoid</span>
                </button>
                <button
                  type="button"
                  className="ob-btn ob-btn-primary"
                  disabled={submitting || loadingMore || exiting}
                  aria-label={`Rate ${current.name}`}
                  aria-expanded={false}
                  onClick={() => setRateMode(true)}
                >
                  <span className="ob-btn-label">Rate it</span>
                  <span className="ob-btn-hint">Bad → Favorite</span>
                </button>
              </div>
            ) : (
              <div
                className="ob-rate"
                role="group"
                aria-label={`Rate ${current.name}`}
              >
                <div className="ob-rate-head">
                  <p className="ob-rate-prompt" id="ob-rate-prompt">
                    How was it for you?
                  </p>
                  <button
                    type="button"
                    className="ob-back"
                    disabled={submitting || exiting}
                    onClick={() => setRateMode(false)}
                  >
                    ← Back
                  </button>
                </div>
                <div className="ob-rate-grid" aria-labelledby="ob-rate-prompt">
                  {RATE_OPTIONS.map((opt, idx) => (
                    <button
                      key={opt.action}
                      ref={idx === 0 ? rateFirstRef : undefined}
                      type="button"
                      className={`ob-rate-btn ${opt.className}`}
                      disabled={submitting || loadingMore || exiting}
                      onClick={() => void applyAction(opt.action)}
                      aria-label={`${opt.label}: ${opt.hint}`}
                    >
                      <span className="ob-rate-emoji" aria-hidden="true">
                        {opt.emoji}
                      </span>
                      <span className="ob-rate-label">{opt.label}</span>
                      <span className="ob-rate-hint">{opt.hint}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {canFinish && (
              <button
                type="button"
                className="ob-finish"
                disabled={submitting || exiting}
                onClick={() => void finish(reactions)}
              >
                See my recommendations →
              </button>
            )}

            {error && (
              <p className="form-error ob-error" role="alert">
                {error}
              </p>
            )}
            {loadingMore && (
              <p className="ob-loading-more" role="status">
                Loading more titles…
              </p>
            )}
          </div>
        </article>
      )}
    </div>
  );
}
