import { useState } from "react";
import type { OnboardingAction, Title } from "../api/titles";
import { RATE_OPTIONS } from "../features/onboarding/constants";
import {
  useOnboardingDeck,
  type DeckOptions,
} from "../features/onboarding/useOnboardingDeck";
import { posterSrc, posterSrcSet, yearOf } from "../lib/poster";

export function OnboardingPage() {
  return <OnboardingDeckView options={{ mode: "account" }} />;
}

/**
 * The rating grid, for onboarding and for guests trying the product.
 *
 * Every poster carries the four answers; anything left alone counts as
 * "haven't seen it". The bar at the bottom says how close the picks are and
 * holds the one button that matters.
 */
export function OnboardingDeckView({ options }: { options: DeckOptions }) {
  const guest = options.mode === "guest";
  const {
    cards,
    reactionFor,
    rate,
    loadMore,
    finish,
    discardCarried,
    error,
    loading,
    loadingMore,
    submitting,
    exhausted,
    ratedCount,
    positiveCount,
    carriedCount,
    canFinish,
    progressPct,
    minRatings,
    minPositive,
  } = useOnboardingDeck(options);

  if (loading) {
    return (
      <div className="ob-stage ob-stage-loading" role="status">
        <div className="spinner" aria-hidden="true" />
        <p className="ob-loading-copy">Picking films you might know…</p>
      </div>
    );
  }

  if (submitting) {
    return (
      <div className="ob-stage ob-stage-loading" role="status">
        <div className="spinner" aria-hidden="true" />
        <p className="eyebrow">Almost there</p>
        <h1 className="ob-loading-title">
          {guest ? "Finding picks for you" : "Building your taste profile"}
        </h1>
        <p className="ob-loading-copy">
          {guest
            ? "Matching your ratings against the whole catalog…"
            : "Weaving your ratings into a personal For You slate…"}
        </p>
      </div>
    );
  }

  if (cards.length === 0) {
    return (
      <div className="ob-stage ob-stage-empty">
        <p className="eyebrow">Onboarding</p>
        <h1>No titles available</h1>
        <p className="lede">
          {error || "Seed the catalog first so there are films to rate."}
        </p>
      </div>
    );
  }

  const remaining = Math.max(0, minRatings - ratedCount);
  const needsPositive = remaining === 0 && positiveCount < minPositive;
  const hint = remaining > 0
    ? `Rate ${remaining} more to ${guest ? "see your picks" : "unlock For You"}`
    : needsPositive
      ? guest
        ? "Add one you liked"
        : `Add ${minPositive - positiveCount} more you liked`
      : "Ready — more ratings make sharper picks";

  return (
    <div className="ob-stage ob-grid-stage">
      <header className="ob-grid-header">
        <p className="eyebrow">{guest ? "Quick taste check" : "Taste calibration"}</p>
        <h1>Rate what you know</h1>
        <p className="ob-sub">
          {guest ? (
            <>
              Tap a rating on {minRatings} films you&apos;ve seen and we&apos;ll
              pick films for you — no account needed. Skip anything unfamiliar.
            </>
          ) : (
            <>
              Tap a rating on films you&apos;ve seen. Skip the rest — an
              unrated poster tells us nothing, and that&apos;s fine.
            </>
          )}
        </p>
        {carriedCount > 0 && !guest && (
          <p className="ob-carried" role="status">
            {carriedCount} answer{carriedCount === 1 ? "" : "s"} carried over from
            before you signed up.{" "}
            <button type="button" className="link-button" onClick={discardCarried}>
              Not yours? Start fresh
            </button>
          </p>
        )}
      </header>

      <ul className="ob-grid" aria-label="Films to rate">
        {cards.map((title) => (
          <RateTile
            key={title.id}
            title={title}
            answer={reactionFor(title.id)}
            onRate={(action) => rate(title.id, action)}
          />
        ))}
      </ul>

      <div className="ob-more">
        {exhausted ? (
          <p className="meta-line">That&apos;s every film we have to show for now.</p>
        ) : (
          <button
            type="button"
            className="btn ghost"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading…" : "Show more films"}
          </button>
        )}
      </div>

      <div className="ob-dock" role="region" aria-label="Progress">
        <div className="ob-dock-inner">
          <div className="ob-dock-progress">
            <p className="ob-progress-count" aria-live="polite" aria-atomic="true">
              <strong>{ratedCount}</strong>
              <span className="ob-progress-of"> of {minRatings} rated</span>
            </p>
            <div
              className="ob-progress-bar"
              role="progressbar"
              aria-valuenow={Math.min(ratedCount, minRatings)}
              aria-valuemin={0}
              aria-valuemax={minRatings}
              aria-label="Rating progress"
            >
              <div className="ob-progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
            <p className="ob-progress-hint">{error ?? hint}</p>
          </div>
          <button
            type="button"
            className="btn primary ob-dock-cta"
            disabled={!canFinish}
            onClick={() => void finish()}
          >
            {guest ? "Show my picks →" : "See my recommendations →"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RateTile({
  title,
  answer,
  onRate,
}: {
  title: Title;
  answer: OnboardingAction | undefined;
  onRate: (action: OnboardingAction) => void;
}) {
  const [failed, setFailed] = useState(false);
  const src = posterSrc(title);
  const srcSet = posterSrcSet(title);
  const year = yearOf(title);
  const chosen = RATE_OPTIONS.find((o) => o.action === answer);

  return (
    <li className={`ob-tile${chosen ? " ob-tile-rated" : ""}`}>
      <div className="ob-tile-poster">
        {src && !failed ? (
          <img
            src={src}
            srcSet={srcSet ?? undefined}
            sizes={srcSet ? "(max-width: 560px) 46vw, (max-width: 960px) 30vw, 200px" : undefined}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            onError={() => setFailed(true)}
          />
        ) : (
          <div className="ob-tile-fallback" aria-hidden="true">
            {title.name}
          </div>
        )}
        {chosen && (
          <span className="ob-tile-badge" aria-hidden="true">
            {chosen.emoji} {chosen.label}
          </span>
        )}
        <div className="ob-tile-rate" role="group" aria-label={`Rate ${title.name}`}>
          {RATE_OPTIONS.map((opt) => (
            <button
              key={opt.action}
              type="button"
              className={`ob-tile-btn ${opt.className}`}
              aria-pressed={answer === opt.action}
              aria-label={`${opt.label} — ${title.name}`}
              title={opt.label}
              onClick={() => onRate(opt.action)}
            >
              <span aria-hidden="true">{opt.emoji}</span>
            </button>
          ))}
        </div>
      </div>
      <h3 className="ob-tile-title">{title.name}</h3>
      {year && <p className="ob-tile-year">{year}</p>}
    </li>
  );
}
