import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { getForYou, interact } from "../../api/titles";
import type { RecommendationItem } from "../../api/titles";
import {
  ACTION_TOAST_MS,
  FEEDBACK_ACTION_LABELS,
  type FeedbackAction,
} from "../../components/ActionToast";
import { useAuth } from "../auth/AuthContext";

export type ForYouWelcome = {
  fromOnboarding?: boolean;
  ratingsCount?: number;
} | null;

export type ForYouUndoToast = {
  item: RecommendationItem;
  action: FeedbackAction;
  message: string;
  index: number;
};

/** How long a card takes to fade out before the row closes the gap. */
const LEAVE_MS = 180;

/**
 * For You slate state: load, act on any card optimistically, undo.
 * Keeps HomePage presentational.
 */
export function useForYouQueue() {
  const { accessToken, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [welcome, setWelcome] = useState<ForYouWelcome>(null);
  const [toast, setToast] = useState<ForYouUndoToast | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [leaving, setLeaving] = useState<ReadonlySet<string>>(new Set());
  const [reloadToken, setReloadToken] = useState(0);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Synchronous guard: two clicks in one frame both see stale React state.
  const inFlight = useRef<Set<string>>(new Set());

  const needsOnboarding = !user?.onboarding_completed_at;

  useEffect(() => {
    const state = (location.state as ForYouWelcome) || null;
    if (state?.fromOnboarding) {
      setWelcome(state);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  useEffect(() => {
    if (!accessToken || needsOnboarding) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await getForYou(accessToken);
        if (!cancelled) setItems(data.items);
      } catch (err) {
        if (!cancelled) {
          setItems([]);
          setError(
            err instanceof ApiError
              ? err.message
              : "Could not load recommendations",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, needsOnboarding, reloadToken]);

  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  const dismissToast = useCallback(() => {
    if (toastTimer.current) {
      clearTimeout(toastTimer.current);
      toastTimer.current = null;
    }
    setToast(null);
  }, []);

  const showUndoToast = useCallback((next: ForYouUndoToast) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(next);
    toastTimer.current = setTimeout(() => {
      setToast(null);
      toastTimer.current = null;
    }, ACTION_TOAST_MS);
  }, []);

  const restore = useCallback((item: RecommendationItem, index: number) => {
    setItems((prev) => {
      if (prev.some((i) => i.title.id === item.title.id)) return prev;
      const next = [...prev];
      next.splice(Math.min(Math.max(index, 0), next.length), 0, item);
      return next;
    });
  }, []);

  const act = useCallback(
    async (item: RecommendationItem, event: FeedbackAction) => {
      const titleId = item.title.id;
      if (!accessToken || inFlight.current.has(titleId)) return;
      inFlight.current.add(titleId);
      const index = items.findIndex((i) => i.title.id === titleId);
      setError(null);

      // Optimistic: fade the card, close the gap, reconcile with the API after.
      setLeaving((prev) => new Set(prev).add(titleId));
      await new Promise((r) => setTimeout(r, LEAVE_MS));
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
      setLeaving((prev) => {
        const next = new Set(prev);
        next.delete(titleId);
        return next;
      });
      showUndoToast({
        item,
        action: event,
        message: `${FEEDBACK_ACTION_LABELS[event]} · ${item.title.name}`,
        index,
      });

      try {
        await interact(accessToken, titleId, event);
      } catch (err) {
        restore(item, index);
        dismissToast();
        setError(err instanceof ApiError ? err.message : "Action failed");
      } finally {
        inFlight.current.delete(titleId);
      }
    },
    [accessToken, items, showUndoToast, dismissToast, restore],
  );

  const undoLast = useCallback(async () => {
    if (!accessToken || !toast || undoBusy) return;
    const { item, index } = toast;
    setUndoBusy(true);
    setError(null);
    try {
      await interact(accessToken, item.title.id, "clear");
      restore(item, index);
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
    }
  }, [accessToken, toast, undoBusy, dismissToast, restore]);

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  return {
    needsOnboarding,
    items,
    error,
    loading,
    welcome,
    toast,
    undoBusy,
    leaving,
    act,
    undoLast,
    dismissToast,
    reload,
  };
}
