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
 * State for the rating grid: a page of posters, an answer per poster, finish.
 *
 * A grid rather than one card at a time: people rate the films they know and
 * scroll past the rest, so a poster left alone *is* "haven't seen it" and costs
 * no click. Six ratings take seconds instead of six screens.
 *
 * The same grid serves guests (answers stay in the browser and go to the
 * stateless guest endpoint) and accounts (answers are stored). Whatever a
 * guest answered is carried into account onboarding, so signing up never
 * means answering the same posters twice. Presentation stays in the page.
 */
export function useOnboardingDeck(options: DeckOptions = { mode: "account" }) {
  const guest = options.mode === "guest";
  const { accessToken, user, refreshUser } = useAuth();
  const navigate = useNavigate();
  // Read once: the draft is the starting point, later writes come from here.
  const [initialDraft] = useState(() => loadGuestDraft());
  const [cards, setCards] = useState<Title[]>([]);
  const [reactions, setReactions] = useState<OnboardingReaction[]>(initialDraft.reactions);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [exhausted, setExhausted] = useState(false);
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

  useEffect(() => {
    if (!guest && user?.onboarding_completed_at) {
      navigate("/", { replace: true });
      return;
    }
    if (!guest && !accessToken) return;

    let cancelled = false;
    (async () => {
      try {
        // Posters already answered as a guest are not asked again.
        const items = await fetchCards([...carriedIds.current]);
        if (cancelled || items === null) return;
        setCards(items);
        setExhausted(items.length === 0);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Could not load titles to rate",
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
    // would reload the grid, and throw away the answers being given, every
    // time refreshUser returns a new object.
  }, [guest, accessToken, user?.onboarding_completed_at, navigate, fetchCards]);

  const byTitle = useMemo(
    () => new Map(reactions.map((r) => [r.title_id, r.action])),
    [reactions],
  );
  const ratedCount = useMemo(
    () => reactions.filter((r) => isRating(r.action)).length,
    [reactions],
  );
  const positiveCount = useMemo(
    () => reactions.filter((r) => isPositive(r.action)).length,
    [reactions],
  );
  const carriedCount = useMemo(
    () => reactions.filter((r) => carriedIds.current.has(r.title_id)).length,
    [reactions],
  );
  const canFinish =
    ratedCount >= minRatings && positiveCount >= minPositive && !submitting;
  const progressPct = Math.min(100, (ratedCount / minRatings) * 100);

  const reactionFor = useCallback((titleId: string) => byTitle.get(titleId), [byTitle]);

  /** Answer a poster; choosing the same answer again takes it back. */
  const rate = useCallback(
    (titleId: string, action: OnboardingAction) => {
      setError(null);
      setReactions((prev) => {
        const same = prev.some((r) => r.title_id === titleId && r.action === action);
        const next = prev.filter((r) => r.title_id !== titleId);
        if (!same) next.push({ title_id: titleId, action });
        if (guest) saveGuestDraft({ reactions: next, saved: loadGuestDraft().saved });
        return next;
      });
    },
    [guest],
  );

  const loadMore = useCallback(async () => {
    if (loadingMore || exhausted) return;
    setLoadingMore(true);
    setError(null);
    try {
      const exclude = [
        ...new Set([
          ...carriedIds.current,
          ...cards.map((c) => c.id),
          ...reactions.map((r) => r.title_id),
        ]),
      ];
      const items = await fetchCards(exclude);
      if (!items || items.length === 0) {
        setExhausted(true);
        return;
      }
      setCards((prev) => [...prev, ...items]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load more titles");
    } finally {
      setLoadingMore(false);
    }
  }, [cards, exhausted, fetchCards, loadingMore, reactions]);

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

  const finish = useCallback(async () => {
    const ratings = reactions.filter((r) => isRating(r.action)).length;
    const positives = reactions.filter((r) => isPositive(r.action)).length;
    if (ratings < minRatings) {
      setError(`Rate at least ${minRatings} films you've seen. You've rated ${ratings}.`);
      return;
    }
    if (positives < minPositive) {
      setError(
        minPositive === 1
          ? "Mark at least one film you liked so we know what to look for."
          : `Mark at least ${minPositive} films you liked so we know what to look for.`,
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      if (onGuestFinish) {
        await onGuestFinish(reactions);
        setSubmitting(false);
      } else {
        await finishAccount(reactions, ratings);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not finish onboarding");
      setSubmitting(false);
    }
  }, [finishAccount, onGuestFinish, minRatings, minPositive, reactions]);

  /** Forget answers carried over from guest mode (e.g. a shared computer). */
  const discardCarried = useCallback(() => {
    const carried = carriedIds.current;
    carriedIds.current = new Set();
    clearGuestDraft();
    setReactions((prev) => prev.filter((r) => !carried.has(r.title_id)));
  }, []);

  return {
    cards,
    reactionFor,
    rate,
    loadMore,
    finish,
    discardCarried,
    reactions,
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
  };
}
