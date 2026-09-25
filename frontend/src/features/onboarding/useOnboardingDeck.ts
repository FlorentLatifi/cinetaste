import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as guestApi from "../../api/guest";
import * as titlesApi from "../../api/titles";
import type { OnboardingAction, OnboardingReaction, Title } from "../../api/titles";
import { useAuth } from "../auth/AuthContext";
import { clearGuestDraft, loadGuestDraft, saveGuestDraft } from "../guest/guestDraft";
import {
  BATCH_SIZE,
  GUEST_MIN_POSITIVE,
  GUEST_MIN_RATINGS,
  isPositive,
  isRating,
  MIN_POSITIVE,
  MIN_RATINGS,
} from "./constants";

export type DeckOptions =
  | { mode: "account" }
  | {
      mode: "guest";
      /** Called with every answer once the gate is met; shows the results. */
      onGuestFinish: (reactions: OnboardingReaction[]) => Promise<void>;
    };

/**
 * Onboarding deck state machine: load batches, apply reactions, finish.
 *
 * The same deck serves guests (answers stay in the browser and go to the
 * stateless guest endpoint) and accounts (answers are stored). Whatever a
 * guest answered is carried into account onboarding, so signing up never
 * means answering the same cards twice. Presentation stays in the page.
 */
export function useOnboardingDeck(options: DeckOptions = { mode: "account" }) {
  const guest = options.mode === "guest";
  const { accessToken, user, refreshUser } = useAuth();
  const navigate = useNavigate();
  // Read once: the draft is the starting point, later writes come from here.
  const [initialDraft] = useState(() => loadGuestDraft());
  const [cards, setCards] = useState<Title[]>([]);
  const [index, setIndex] = useState(0);
  const [reactions, setReactions] = useState<OnboardingReaction[]>(initialDraft.reactions);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [rateMode, setRateMode] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [cardAnimKey, setCardAnimKey] = useState(0);
  const [exiting, setExiting] = useState(false);
  const carriedIds = useRef(new Set(initialDraft.reactions.map((r) => r.title_id)));

  const minRatings = guest ? GUEST_MIN_RATINGS : MIN_RATINGS;
  const minPositive = guest ? GUEST_MIN_POSITIVE : MIN_POSITIVE;
  const onGuestFinish = guest ? options.onGuestFinish : null;

  const fetchCards = useCallback(
    async (exclude: string[]) => {
      if (guest) return (await guestApi.getGuestCards({ limit: BATCH_SIZE, exclude })).items;
      if (!accessToken) return null;
      return (await titlesApi.getOnboardingCards(accessToken, { limit: BATCH_SIZE, exclude }))
        .items;
    },
    [guest, accessToken],
  );

  const loadBatch = useCallback(
    async (exclude: string[], replace: boolean) => {
      const items = await fetchCards(exclude);
      if (items === null) return;
      if (items.length === 0) {
        setExhausted(true);
        return;
      }
      setCards((prev) => (replace ? items : [...prev, ...items]));
      setExhausted(false);
    },
    [fetchCards],
  );

  useEffect(() => {
    if (!guest && user?.onboarding_completed_at) {
      navigate("/", { replace: true });
      return;
    }
    if (!guest && !accessToken) return;

    let cancelled = false;
    (async () => {
      try {
        // Cards already answered as a guest are not asked again.
        await loadBatch([...carriedIds.current], true);
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
    // Only the completion stamp matters here: depending on the whole user
    // would reload the deck, and throw away the cards being answered, every
    // time refreshUser returns a new object.
  }, [guest, accessToken, user?.onboarding_completed_at, navigate, loadBatch]);

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
  const carriedCount = useMemo(
    () => reactions.filter((r) => carriedIds.current.has(r.title_id)).length,
    [reactions],
  );
  const canFinish =
    ratedCount >= minRatings && positiveCount >= minPositive && !submitting;

  const seenIds = useMemo(
    () => [...new Set([...carriedIds.current, ...cards.map((c) => c.id)])],
    [cards],
  );
  const progressPct = Math.min(100, (ratedCount / minRatings) * 100);

  const ensureMoreCardsIfNeeded = useCallback(
    async (nextIndex: number, nextReactions: OnboardingReaction[]) => {
      const remaining = cards.length - nextIndex;
      const ratingsSoFar = nextReactions.filter((r) => isRating(r.action)).length;
      if (remaining > 3 || exhausted) return;
      if (ratingsSoFar >= minRatings && remaining > 0) return;

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
    [cards.length, exhausted, loadBatch, seenIds, minRatings],
  );

  const advanceCard = useCallback(() => {
    setExiting(false);
    setCardAnimKey((k) => k + 1);
  }, []);

  const finishAccount = useCallback(
    async (finalReactions: OnboardingReaction[], ratings: number) => {
      if (!accessToken) return;
      try {
        await titlesApi.completeOnboarding(accessToken, finalReactions);
      } catch (err) {
        if (err instanceof ApiError && err.code === "unknown_titles" && carriedIds.current.size) {
          // A title answered as a guest has since left the catalog. Drop the
          // carried answers rather than leave the person stuck on a 400.
          const fresh = finalReactions.filter((r) => !carriedIds.current.has(r.title_id));
          carriedIds.current = new Set();
          clearGuestDraft();
          setReactions(fresh);
          throw new ApiError(
            400,
            "unknown_titles",
            "A few earlier answers are no longer in the catalog. Rate a few more to finish.",
          );
        }
        throw err;
      }
      // Picks saved as a guest become the start of their watchlist. Best
      // effort: onboarding is already done, and a failure here only costs
      // a saved title, not the account.
      const saved = loadGuestDraft().saved;
      await Promise.allSettled(
        saved.map((id) => titlesApi.interact(accessToken, id, "watchlist")),
      );
      clearGuestDraft();
      try {
        await refreshUser();
      } catch {
        // Still navigate; home may re-fetch user on next load
      }
      navigate("/", {
        replace: true,
        state: { fromOnboarding: true, ratingsCount: ratings },
      });
    },
    [accessToken, navigate, refreshUser],
  );

  const finish = useCallback(
    async (finalReactions: OnboardingReaction[]) => {
      const ratings = finalReactions.filter((r) => isRating(r.action)).length;
      const positives = finalReactions.filter((r) => isPositive(r.action)).length;
      if (ratings < minRatings) {
        setError(
          `Rate at least ${minRatings} titles you've actually seen. ` +
            `Current: ${ratings}. “Haven't seen it” does not count.`,
        );
        setExiting(false);
        return;
      }
      if (positives < minPositive) {
        setError(
          minPositive === 1
            ? "Mark at least one title you liked so we know what to look for."
            : `Mark at least ${minPositive} as OK, Good, or Favorite so we know what you like.`,
        );
        setExiting(false);
        return;
      }

      setSubmitting(true);
      setError(null);
      try {
        if (onGuestFinish) {
          await onGuestFinish(finalReactions);
          setSubmitting(false);
        } else {
          await finishAccount(finalReactions, ratings);
        }
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
    [finishAccount, onGuestFinish, minRatings, minPositive],
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
      if (guest) {
        saveGuestDraft({ reactions: nextReactions, saved: loadGuestDraft().saved });
      }

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

      if (nextRated >= minRatings && nextPositive >= minPositive) {
        await finish(nextReactions);
        return;
      }

      setLoadingMore(true);
      try {
        const exclude = [
          ...new Set([...seenIds, ...nextReactions.map((r) => r.title_id)]),
        ];
        const items = await fetchCards(exclude);
        if (!items || items.length === 0) {
          setExhausted(true);
          setError(
            `We need ${minRatings} ratings of titles you've seen (you have ${nextRated}). ` +
              "“Haven't seen it” doesn't count — keep going when more titles load, or seed the catalog.",
          );
          setExiting(false);
          setReactions(nextReactions);
          return;
        }
        setCards((prev) => [...prev, ...items]);
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
      advanceCard,
      cards.length,
      current,
      ensureMoreCardsIfNeeded,
      exiting,
      fetchCards,
      finish,
      guest,
      index,
      minPositive,
      minRatings,
      reactions,
      seenIds,
      submitting,
    ],
  );

  /** Forget answers carried over from guest mode (e.g. a shared computer). */
  const discardCarried = useCallback(() => {
    const carried = carriedIds.current;
    carriedIds.current = new Set();
    clearGuestDraft();
    setReactions((prev) => prev.filter((r) => !carried.has(r.title_id)));
  }, []);

  return {
    discardCarried,
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
    carriedCount,
    canFinish,
    progressPct,
    applyAction,
    finish,
    minRatings,
    minPositive,
  };
}
