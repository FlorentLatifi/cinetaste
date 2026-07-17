import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as titlesApi from "../../api/titles";
import type { RecommendationItem } from "../../api/titles";
import {
  ACTION_TOAST_MS,
  FEEDBACK_ACTION_LABELS,
  type FeedbackAction,
} from "../../components/ActionToast";
import { useAuth } from "../auth/AuthContext";
import { heroPosterUrl } from "../../lib/poster";

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

/**
 * For You slate state: load, optimistic act, undo, keyboard-ready actions.
 * Keeps HomePage presentational.
 */
export function useForYouQueue() {
  const { accessToken, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [welcome, setWelcome] = useState<ForYouWelcome>(null);
  const [toast, setToast] = useState<ForYouUndoToast | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [cardKey, setCardKey] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const needsOnboarding = !user?.onboarding_completed_at;
  const current = items[0] ?? null;
  const remaining = Math.max(0, items.length - 1);

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
        const data = await titlesApi.getForYou(accessToken);
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

  // Prefetch the next poster so slate advances feel instant
  useEffect(() => {
    const next = items[1];
    if (!next) return;
    const url = heroPosterUrl(next.title);
    if (!url) return;
    const img = new Image();
    img.decoding = "async";
    img.src = url;
  }, [items]);

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

  const act = useCallback(
    async (event: FeedbackAction) => {
      if (!accessToken || !current || busy || exiting) return;
      const item = current;
      const titleId = item.title.id;
      const index = 0;

      setBusy(true);
      setError(null);
      setExiting(true);

      // Optimistic UI: animate out, advance slate immediately, reconcile API after.
      await new Promise((r) => setTimeout(r, 160));
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
      setCardKey((k) => k + 1);
      setExiting(false);
      showUndoToast({
        item,
        action: event,
        message: `${FEEDBACK_ACTION_LABELS[event]} · ${item.title.name}`,
        index,
      });

      try {
        await titlesApi.interact(accessToken, titleId, event);
      } catch (err) {
        setItems((prev) => {
          if (prev.some((i) => i.title.id === titleId)) return prev;
          const next = [...prev];
          const at = Math.min(Math.max(index, 0), next.length);
          next.splice(at, 0, item);
          return next;
        });
        setCardKey((k) => k + 1);
        dismissToast();
        setError(err instanceof ApiError ? err.message : "Action failed");
      } finally {
        setBusy(false);
      }
    },
    [accessToken, current, busy, exiting, showUndoToast, dismissToast],
  );

  const undoLast = useCallback(async () => {
    if (!accessToken || !toast || undoBusy) return;
    const { item, index } = toast;
    setUndoBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, item.title.id, "clear");
      setItems((prev) => {
        if (prev.some((i) => i.title.id === item.title.id)) return prev;
        const next = [...prev];
        const at = Math.min(Math.max(index, 0), next.length);
        next.splice(at, 0, item);
        return next;
      });
      setCardKey((k) => k + 1);
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
    }
  }, [accessToken, toast, undoBusy, dismissToast]);

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  return {
    needsOnboarding,
    items,
    current,
    remaining,
    error,
    loading,
    busy,
    welcome,
    toast,
    undoBusy,
    exiting,
    cardKey,
    act,
    undoLast,
    dismissToast,
    reload,
  };
}
