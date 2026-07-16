import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { RecommendationItem } from "../api/titles";
import {
  ActionToast,
  ACTION_TOAST_MS,
  FEEDBACK_ACTION_LABELS,
  type FeedbackAction,
} from "../components/ActionToast";
import { PosterCard } from "../components/PosterCard";
import { useAuth } from "../features/auth/AuthContext";

type LocationState = {
  fromOnboarding?: boolean;
  ratingsCount?: number;
} | null;

type UndoToast = {
  item: RecommendationItem;
  action: FeedbackAction;
  message: string;
  index: number;
};

export function HomePage() {
  const { accessToken, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [welcome, setWelcome] = useState<LocationState>(null);
  const [toast, setToast] = useState<UndoToast | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const needsOnboarding = !user?.onboarding_completed_at;

  useEffect(() => {
    const state = (location.state as LocationState) || null;
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
    (async () => {
      try {
        const data = await titlesApi.getForYou(accessToken);
        if (!cancelled) setItems(data.items);
      } catch (err) {
        if (!cancelled) {
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
  }, [accessToken, needsOnboarding]);

  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  function dismissToast() {
    if (toastTimer.current) {
      clearTimeout(toastTimer.current);
      toastTimer.current = null;
    }
    setToast(null);
  }

  function showUndoToast(next: UndoToast) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(next);
    toastTimer.current = setTimeout(() => {
      setToast(null);
      toastTimer.current = null;
    }, ACTION_TOAST_MS);
  }

  async function act(titleId: string, event: FeedbackAction) {
    if (!accessToken) return;
    const index = items.findIndex((i) => i.title.id === titleId);
    if (index < 0) return;
    const item = items[index];

    setBusyId(titleId);
    setError(null);
    try {
      await titlesApi.interact(accessToken, titleId, event);
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
      showUndoToast({
        item,
        action: event,
        message: `${FEEDBACK_ACTION_LABELS[event]} · ${item.title.name}`,
        index,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  }

  async function undoLast() {
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
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
    }
  }

  if (needsOnboarding) {
    return (
      <section className="hero-panel">
        <p className="eyebrow">Almost there</p>
        <h1>Train your taste in under two minutes</h1>
        <p className="lede">
          Rate movies you know — skip the rest. We build an explainable profile
          and a personal For You slate, not a popularity dump.
        </p>
        <Link className="btn primary" to="/onboarding">
          Start onboarding
        </Link>
      </section>
    );
  }

  if (loading) {
    return (
      <div className="center-inline home-loading" role="status" aria-live="polite">
        <div className="spinner" aria-hidden="true" />
        <p className="ob-loading-copy">
          {welcome?.fromOnboarding
            ? "Fetching your first personal picks…"
            : "Loading For You…"}
        </p>
      </div>
    );
  }

  return (
    <section className="feed" aria-labelledby="for-you-heading">
      {welcome?.fromOnboarding && (
        <div className="welcome-banner" role="status" aria-live="polite">
          <p className="eyebrow">Profile ready</p>
          <h2 className="welcome-title">Your first recommendations</h2>
          <p className="welcome-copy">
            Built from{" "}
            <strong>
              {welcome.ratingsCount ?? "your"} rating
              {(welcome.ratingsCount ?? 2) === 1 ? "" : "s"}
            </strong>
            . Every card explains why it matches your taste.
          </p>
        </div>
      )}

      <div className="feed-header">
        <div>
          <p className="eyebrow">For you</p>
          <h1 id="for-you-heading">
            {welcome?.fromOnboarding
              ? "Picks matched to you"
              : "Picks matched to your taste"}
          </h1>
          <p className="lede">Posters first — every pick includes a short reason.</p>
        </div>
        <Link className="btn ghost" to="/watchlist">
          Watchlist
        </Link>
      </div>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      {!items.length && (
        <div className="callout" role="status">
          No recommendations yet. Seed the catalog and complete onboarding with a
          few ratings.
        </div>
      )}

      <ul className="poster-grid for-you" aria-label="Recommended titles">
        {items.map((item) => {
          const name = item.title.name;
          const busy = busyId === item.title.id;
          const badges = (
            <>
              {item.reasons.some((r) => r.code === "hidden_gem") && (
                <span className="rec-badge gem">Hidden gem</span>
              )}
              {item.reasons.some((r) => r.code === "discovery") && (
                <span className="rec-badge discovery">Discovery</span>
              )}
            </>
          );
          const hasBadge =
            item.reasons.some((r) => r.code === "hidden_gem") ||
            item.reasons.some((r) => r.code === "discovery");

          return (
            <li key={item.title.id}>
              <PosterCard
                title={item.title}
                badge={hasBadge ? <div className="rec-badges">{badges}</div> : undefined}
              >
                {item.reasons.length > 0 && (
                  <div className="why-block">
                    <p className="why-label" id={`why-${item.title.id}`}>
                      Why this pick
                    </p>
                    <ul
                      className="reasons"
                      aria-labelledby={`why-${item.title.id}`}
                    >
                      {item.reasons.slice(0, 2).map((r, idx) => (
                        <li
                          key={`${r.code}-${idx}`}
                          className={idx === 0 ? "reason-primary" : undefined}
                        >
                          {r.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div
                  className="rec-actions poster-actions"
                  role="group"
                  aria-label={`Actions for ${name}`}
                >
                  <button
                    type="button"
                    className="btn ghost btn-sm"
                    disabled={busy}
                    aria-label={`Pass on ${name}`}
                    onClick={() => void act(item.title.id, "dislike")}
                  >
                    Pass
                  </button>
                  <button
                    type="button"
                    className="btn ghost btn-sm"
                    disabled={busy}
                    aria-label={`Save ${name} to watchlist`}
                    onClick={() => void act(item.title.id, "watchlist")}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="btn primary btn-sm"
                    disabled={busy}
                    aria-label={`Like ${name}`}
                    onClick={() => void act(item.title.id, "like")}
                  >
                    Like
                  </button>
                </div>
              </PosterCard>
            </li>
          );
        })}
      </ul>

      {toast && (
        <ActionToast
          message={toast.message}
          undoBusy={undoBusy}
          onUndo={() => void undoLast()}
          onDismiss={dismissToast}
        />
      )}
    </section>
  );
}
