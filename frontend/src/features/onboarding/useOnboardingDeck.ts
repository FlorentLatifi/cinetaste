import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as titlesApi from "../../api/titles";
import type { OnboardingAction, OnboardingReaction, Title } from "../../api/titles";
import { useAuth } from "../auth/AuthContext";
import {
  BATCH_SIZE,
  isPositive,
  isRating,
  MIN_POSITIVE,
  MIN_RATINGS,
} from "./constants";

/**
 * Onboarding deck state machine: load batches, apply reactions, finish.
 * Presentation stays in OnboardingPage.
 */
export function useOnboardingDeck() {
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

  const ensureMoreCardsIfNeeded = useCallback(
    async (nextIndex: number, nextReactions: OnboardingReaction[]) => {
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
    },
    [accessToken, cards.length, exhausted, loadBatch, seenIds],
  );

  const advanceCard = useCallback(() => {
    setExiting(false);
    setCardAnimKey((k) => k + 1);
  }, []);

  const finish = useCallback(
    async (finalReactions: OnboardingReaction[]) => {
      if (!accessToken) return;
      const ratings = finalReactions.filter((r) => isRating(r.action)).length;
      const positives = finalReactions.filter((r) =>
        isPositive(r.action),
      ).length;
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
          err instanceof ApiError
            ? err.message
            : "Could not finish onboarding",
        );
        setSubmitting(false);
        setExiting(false);
      }
    },
    [accessToken, navigate, refreshUser],
  );

  const applyAction = useCallback(
    async (action: OnboardingAction) => {
      if (!current || submitting || exiting) return;
      setRateMode(false);
      setError(null);
      setExiting(true);

      const nextReactions = [
        ...reactions.filter((r) => r.title_id !== current.id),
        { title_id: current.id, action },
      ];
      setReactions(nextReactions);

      await new Promise((r) => setTimeout(r, 160));

      const nextIndex = index + 1;
      const nextRated = nextReactions.filter((r) => isRating(r.action)).length;
      const nextPositive = nextReactions.filter((r) =>
        isPositive(r.action),
      ).length;

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
    },
    [
      accessToken,
      advanceCard,
      cards.length,
      current,
      ensureMoreCardsIfNeeded,
      exiting,
      finish,
      index,
      reactions,
      seenIds,
      submitting,
    ],
  );

  return {
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
    minRatings: MIN_RATINGS,
    minPositive: MIN_POSITIVE,
  };
}
