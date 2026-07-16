import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { OnboardingAction, OnboardingReaction, Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

/** Must match backend MIN_ONBOARDING_RATINGS / MIN_ONBOARDING_POSITIVE. */
const MIN_RATINGS = 6;
const MIN_POSITIVE = 2;
/** First batch matches curated primary seed (~15); later batches pull reserve. */
const BATCH_SIZE = 15;

const RATE_OPTIONS: {
  action: OnboardingAction;
  label: string;
  hint: string;
  className: string;
}[] = [
  { action: "rate_1", label: "Bad", hint: "Would not watch again", className: "rate-bad" },
  { action: "rate_2", label: "It's ok", hint: "Fine, not special", className: "rate-ok" },
  { action: "rate_3", label: "Good", hint: "I'd recommend it", className: "rate-good" },
  { action: "rate_4", label: "Favorite", hint: "Peak taste for me", className: "rate-fav" },
];

function isRating(action: OnboardingAction): boolean {
  return action.startsWith("rate_");
}

function isPositive(action: OnboardingAction): boolean {
  return action === "rate_2" || action === "rate_3" || action === "rate_4";
}

export function OnboardingPage() {
  const { accessToken, user } = useAuth();
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

  async function ensureMoreCardsIfNeeded(nextIndex: number, nextReactions: OnboardingReaction[]) {
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

  async function applyAction(action: OnboardingAction) {
    if (!current || submitting) return;
    setRateMode(false);
    setError(null);

    const nextReactions = [
      ...reactions.filter((r) => r.title_id !== current.id),
      { title_id: current.id, action },
    ];
    setReactions(nextReactions);

    const nextIndex = index + 1;
    const nextRated = nextReactions.filter((r) => isRating(r.action)).length;
    const nextPositive = nextReactions.filter((r) => isPositive(r.action)).length;

    if (nextIndex < cards.length) {
      setIndex(nextIndex);
      void ensureMoreCardsIfNeeded(nextIndex, nextReactions);
      return;
    }

    // End of deck
    if (nextRated >= MIN_RATINGS && nextPositive >= MIN_POSITIVE) {
      await finish(nextReactions);
      return;
    }

    // Need more real ratings — fetch another batch
    setLoadingMore(true);
    try {
      const exclude = [...new Set([...seenIds, ...nextReactions.map((r) => r.title_id)])];
      const data = await titlesApi.getOnboardingCards(accessToken!, {
        limit: BATCH_SIZE,
        exclude,
      });
      if (data.items.length === 0) {
        setExhausted(true);
        setError(
          `We need ${MIN_RATINGS} ratings of titles you've seen (you have ${nextRated}). ` +
            "Try finishing with what you have if the catalog is limited, or seed more titles.",
        );
        setReactions(nextReactions);
        return;
      }
      setCards((prev) => [...prev, ...data.items]);
      setIndex(nextIndex);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load more titles");
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
      return;
    }
    if (positives < MIN_POSITIVE) {
      setError(
        `Mark at least ${MIN_POSITIVE} as It's ok, Good, or Favorite so we know what you like.`,
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await titlesApi.completeOnboarding(accessToken, finalReactions);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not finish onboarding");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" />
      </div>
    );
  }

  if (!current && !submitting && !loadingMore) {
    return (
      <div className="center-screen narrow">
        <h1>Onboarding unavailable</h1>
        <p className="lede">
          {error || "No cards available. Seed the catalog first."}
        </p>
        {reactions.length > 0 && (
          <button
            type="button"
            className="btn primary"
            disabled={!canFinish}
            onClick={() => void finish(reactions)}
          >
            Try finish with {ratedCount} ratings
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="onboarding">
      <div className="onboarding-meta">
        <p className="eyebrow">Taste calibration</p>
        <h1>Rate movies you know</h1>
        <p className="lede">
          Skip titles you have not seen — they give us zero signal. Rate the ones
          you know so we learn real taste, not popularity.
        </p>
        <ul className="signal-legend">
          <li>
            <strong>Haven&apos;t seen it</strong> — no effect on your profile
          </li>
          <li>
            <strong>Not interested</strong> — mild “avoid this vibe”
          </li>
          <li>
            <strong>Rate it</strong> — Bad → Favorite builds your vector
          </li>
        </ul>
        <div className="progress-row">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min(100, (ratedCount / MIN_RATINGS) * 100)}%`,
              }}
            />
          </div>
          <span>
            {ratedCount}/{MIN_RATINGS} ratings · {positiveCount} positive
            {unseenCount > 0 ? ` · ${unseenCount} unseen` : ""}
            {loadingMore ? " · loading more…" : ""}
          </span>
        </div>
      </div>

      {current && (
        <article className="swipe-card">
          <div className="swipe-poster">
            {current.poster_url ? (
              <img src={current.poster_url} alt={current.name} />
            ) : (
              <div className="poster-fallback">{current.name}</div>
            )}
          </div>
          <div className="swipe-body">
            <h2>{current.name}</h2>
            <p className="meta-line">
              {current.media_type.toUpperCase()}
              {current.release_date ? ` · ${current.release_date.slice(0, 4)}` : ""}
              {current.vote_average ? ` · ★ ${current.vote_average.toFixed(1)}` : ""}
            </p>
            <p className="genres">
              {current.genres.map((g) => g.name).join(" · ") || "Uncategorized"}
            </p>
            <p className="overview">{current.overview || "No synopsis available."}</p>

            {!rateMode ? (
              <div className="swipe-actions onboarding-primary-actions">
                <button
                  type="button"
                  className="btn ghost"
                  disabled={submitting || loadingMore}
                  onClick={() => void applyAction("haven't_seen")}
                >
                  Haven&apos;t seen it
                </button>
                <button
                  type="button"
                  className="btn danger"
                  disabled={submitting || loadingMore}
                  onClick={() => void applyAction("not_interested")}
                >
                  Not interested
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={submitting || loadingMore}
                  onClick={() => setRateMode(true)}
                >
                  Rate it
                </button>
              </div>
            ) : (
              <div className="rate-panel">
                <p className="rate-prompt">How do you feel about this one?</p>
                <div className="rate-grid">
                  {RATE_OPTIONS.map((opt) => (
                    <button
                      key={opt.action}
                      type="button"
                      className={`btn rate-btn ${opt.className}`}
                      disabled={submitting || loadingMore}
                      onClick={() => void applyAction(opt.action)}
                      title={opt.hint}
                    >
                      <span className="rate-label">{opt.label}</span>
                      <span className="rate-hint">{opt.hint}</span>
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn ghost full"
                  disabled={submitting}
                  onClick={() => setRateMode(false)}
                >
                  Back
                </button>
              </div>
            )}

            {canFinish && (
              <button
                type="button"
                className="btn ghost full"
                disabled={submitting}
                onClick={() => void finish(reactions)}
              >
                {submitting
                  ? "Building your profile…"
                  : `Finish with ${ratedCount} ratings`}
              </button>
            )}
            {error && <p className="form-error">{error}</p>}
          </div>
        </article>
      )}

      {(submitting || (loadingMore && !current)) && (
        <div className="center-inline">
          <div className="spinner" />
        </div>
      )}
    </div>
  );
}
