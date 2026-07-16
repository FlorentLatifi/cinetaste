import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { OnboardingAction, OnboardingReaction, Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

/** Must match backend MIN_ONBOARDING_RATINGS / MIN_ONBOARDING_POSITIVE. */
const MIN_RATINGS = 6;
const MIN_POSITIVE = 2;
const BATCH_SIZE = 15;

const RATE_OPTIONS: {
  action: OnboardingAction;
  label: string;
  hint: string;
  emoji: string;
  className: string;
}[] = [
  {
    action: "rate_1",
    label: "Bad",
    hint: "Not for me",
    emoji: "👎",
    className: "ob-rate-bad",
  },
  {
    action: "rate_2",
    label: "OK",
    hint: "Fine, not special",
    emoji: "😐",
    className: "ob-rate-ok",
  },
  {
    action: "rate_3",
    label: "Good",
    hint: "I'd recommend it",
    emoji: "👍",
    className: "ob-rate-good",
  },
  {
    action: "rate_4",
    label: "Favorite",
    hint: "Peak taste",
    emoji: "✦",
    className: "ob-rate-fav",
  },
];

function isRating(action: OnboardingAction): boolean {
  return action.startsWith("rate_");
}

function isPositive(action: OnboardingAction): boolean {
  return action === "rate_2" || action === "rate_3" || action === "rate_4";
}

function yearOf(title: Title): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}

/** Prefer a larger TMDb size for the hero poster when possible. */
function heroPosterUrl(title: Title): string | null {
  if (!title.poster_url && !title.poster_path) return null;
  const raw = title.poster_path || title.poster_url || "";
  if (raw.startsWith("http") && raw.includes("/w500")) {
    return raw.replace("/w500", "/w780");
  }
  if (raw.startsWith("/") && !raw.startsWith("http")) {
    return `https://image.tmdb.org/t/p/w780${raw}`;
  }
  return title.poster_url;
}

export function OnboardingPage() {
  const { accessToken, user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [cards, setCards] = useState<Title[]>([]);
  const [index, setIndex] = useState(0);
  const [reactions, setReactions] = useState<OnboardingReaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [rateMode, setRateMode] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [cardAnimKey, setCardAnimKey] = useState(0);
  const [exiting, setExiting] = useState(false);
  const rateFirstRef = useRef<HTMLButtonElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  const loadBatch = useCallback(
    async (exclude: string[], replace: boolean) => {
      if (!accessToken) return;
      const data = await titlesApi.getOnboardingCards(accessToken, {
        limit: BATCH_SIZE,
        exclude,
      });
      if (data.items.length === 0) {
        setExhausted(true);
        return;
      }
      setCards((prev) => (replace ? data.items : [...prev, ...data.items]));
      setExhausted(false);
    },
    [accessToken],
  );

  useEffect(() => {
    if (user?.onboarding_completed_at) {
      navigate("/", { replace: true });
      return;
    }
    if (!accessToken) return;

    let cancelled = false;
    (async () => {
      try {
        await loadBatch([], true);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Could not load onboarding cards",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, user, navigate, loadBatch]);

  const current = cards[index];
  const ratedCount = useMemo(
    () => reactions.filter((r) => isRating(r.action)).length,
    [reactions],
  );
  const positiveCount = useMemo(
    () => reactions.filter((r) => isPositive(r.action)).length,
    [reactions],
  );
  const unseenCount = useMemo(
    () => reactions.filter((r) => r.action === "haven't_seen").length,
    [reactions],
  );
  const canFinish =
    ratedCount >= MIN_RATINGS && positiveCount >= MIN_POSITIVE && !submitting;

  const seenIds = useMemo(() => cards.map((c) => c.id), [cards]);
  const progressPct = Math.min(100, (ratedCount / MIN_RATINGS) * 100);
  const poster = current ? heroPosterUrl(current) : null;

  async function ensureMoreCardsIfNeeded(
    nextIndex: number,
    nextReactions: OnboardingReaction[],
  ) {
    const remaining = cards.length - nextIndex;
    const ratingsSoFar = nextReactions.filter((r) => isRating(r.action)).length;
    if (remaining > 3 || exhausted || !accessToken) return;
    if (ratingsSoFar >= MIN_RATINGS && remaining > 0) return;

    setLoadingMore(true);
    try {
      await loadBatch(seenIds, false);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load more titles",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  function advanceCard() {
    setExiting(false);
    setCardAnimKey((k) => k + 1);
    requestAnimationFrame(() => titleRef.current?.focus());
  }

  useEffect(() => {
    if (rateMode) {
      requestAnimationFrame(() => rateFirstRef.current?.focus());
    }
  }, [rateMode]);

  async function applyAction(action: OnboardingAction) {
    if (!current || submitting || exiting) return;
    setRateMode(false);
    setError(null);
    setExiting(true);

    const nextReactions = [
      ...reactions.filter((r) => r.title_id !== current.id),
      { title_id: current.id, action },
    ];
    setReactions(nextReactions);

    // Brief exit animation before swapping the card
    await new Promise((r) => setTimeout(r, 160));

    const nextIndex = index + 1;
    const nextRated = nextReactions.filter((r) => isRating(r.action)).length;
    const nextPositive = nextReactions.filter((r) => isPositive(r.action)).length;

    if (nextIndex < cards.length) {
      setIndex(nextIndex);
      advanceCard();
      void ensureMoreCardsIfNeeded(nextIndex, nextReactions);
      return;
    }

    if (nextRated >= MIN_RATINGS && nextPositive >= MIN_POSITIVE) {
      await finish(nextReactions);
      return;
    }

    setLoadingMore(true);
    try {
      const exclude = [
        ...new Set([...seenIds, ...nextReactions.map((r) => r.title_id)]),
      ];
      const data = await titlesApi.getOnboardingCards(accessToken!, {
        limit: BATCH_SIZE,
        exclude,
      });
      if (data.items.length === 0) {
        setExhausted(true);
        setError(
          `We need ${MIN_RATINGS} ratings of titles you've seen (you have ${nextRated}). ` +
            "“Haven't seen it” doesn't count — keep going when more titles load, or seed the catalog.",
        );
        setExiting(false);
        setReactions(nextReactions);
        return;
      }
      setCards((prev) => [...prev, ...data.items]);
      setIndex(nextIndex);
      advanceCard();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load more titles",
      );
      setExiting(false);
    } finally {
      setLoadingMore(false);
    }
  }

  async function finish(finalReactions: OnboardingReaction[]) {
    if (!accessToken) return;
    const ratings = finalReactions.filter((r) => isRating(r.action)).length;
    const positives = finalReactions.filter((r) => isPositive(r.action)).length;
    if (ratings < MIN_RATINGS) {
      setError(
        `Rate at least ${MIN_RATINGS} titles you've actually seen. ` +
          `Current: ${ratings}. “Haven't seen it” does not count.`,
      );
      setExiting(false);
      return;
    }
    if (positives < MIN_POSITIVE) {
      setError(
        `Mark at least ${MIN_POSITIVE} as OK, Good, or Favorite so we know what you like.`,
      );
      setExiting(false);
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await titlesApi.completeOnboarding(accessToken, finalReactions);
      // Refresh auth user so Home sees onboarding_completed_at without full reload
      try {
        await refreshUser();
      } catch {
        // Still navigate; home may re-fetch user on next load
      }
      navigate("/", {
        replace: true,
        state: {
          fromOnboarding: true,
          ratingsCount: ratings,
        },
      });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not finish onboarding",
      );
      setSubmitting(false);
      setExiting(false);
    }
  }

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
      {/* Ambient poster glow */}
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
              <span className="ob-progress-of"> of {MIN_RATINGS} rated</span>
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
            aria-valuemax={MIN_RATINGS}
            aria-label="Onboarding rating progress"
          >
            <div className="ob-progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <p className="ob-progress-hint">
            {ratedCount < MIN_RATINGS
              ? `${MIN_RATINGS - ratedCount} more rating${MIN_RATINGS - ratedCount === 1 ? "" : "s"} to unlock For You`
              : positiveCount < MIN_POSITIVE
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
                alt=""
                draggable={false}
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
