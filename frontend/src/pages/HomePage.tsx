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
import { useAuth } from "../features/auth/AuthContext";
import { heroPosterUrl, yearOf } from "../lib/poster";

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
  const [busy, setBusy] = useState(false);
  const [welcome, setWelcome] = useState<LocationState>(null);
  const [toast, setToast] = useState<UndoToast | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [cardKey, setCardKey] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  const needsOnboarding = !user?.onboarding_completed_at;
  const current = items[0] ?? null;
  const remaining = Math.max(0, items.length - 1);
  const poster = current ? heroPosterUrl(current.title) : null;

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

  useEffect(() => {
    if (current && !loading) {
      requestAnimationFrame(() => titleRef.current?.focus({ preventScroll: true }));
    }
  }, [current?.title.id, loading]);

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

  async function act(event: FeedbackAction) {
    if (!accessToken || !current || busy || exiting) return;
    const item = current;
    const titleId = item.title.id;
    const index = 0;

    setBusy(true);
    setError(null);
    setExiting(true);
    try {
      await titlesApi.interact(accessToken, titleId, event);
      // Brief exit so the next poster can enter cleanly
      await new Promise((r) => setTimeout(r, 180));
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
      setCardKey((k) => k + 1);
      setExiting(false);
      showUndoToast({
        item,
        action: event,
        message: `${FEEDBACK_ACTION_LABELS[event]} · ${item.title.name}`,
        index,
      });
    } catch (err) {
      setExiting(false);
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
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
      setCardKey((k) => k + 1);
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
    }
  }

  if (needsOnboarding) {
    return (
      <section className="fy-stage fy-stage-gate">
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
      <div className="fy-stage" role="status" aria-live="polite" aria-busy="true">
        <div className="fy-skeleton" aria-hidden="true">
          <div className="fy-skeleton-poster shimmer" />
          <div className="fy-skeleton-line shimmer" />
          <div className="fy-skeleton-line short shimmer" />
          <div className="fy-skeleton-actions">
            <span className="shimmer" />
            <span className="shimmer" />
            <span className="shimmer" />
          </div>
        </div>
        <p className="sr-only">
          {welcome?.fromOnboarding
            ? "Fetching your first personal picks"
            : "Loading For You"}
        </p>
      </div>
    );
  }

  return (
    <section className="fy-stage" aria-labelledby="for-you-heading">
      {poster && (
        <div
          className="fy-ambient"
          style={{ backgroundImage: `url(${poster})` }}
          aria-hidden
        />
      )}

      <header className="fy-header">
        <div className="fy-header-text">
          <p className="eyebrow">For you</p>
          <h1 id="for-you-heading">
            {welcome?.fromOnboarding
              ? "Picks matched to you"
              : "Picks matched to your taste"}
          </h1>
          {welcome?.fromOnboarding ? (
            <p className="fy-sub" role="status">
              Built from{" "}
              <strong>
                {welcome.ratingsCount ?? "your"} rating
                {(welcome.ratingsCount ?? 2) === 1 ? "" : "s"}
              </strong>
              . One poster at a time — every pick explains why.
            </p>
          ) : (
            <p className="fy-sub">
              One poster. Your taste. Pass, save, or like — then the next.
            </p>
          )}
        </div>
        <div className="fy-header-meta">
          {items.length > 0 && (
            <p className="fy-queue" aria-live="polite">
              <strong>{items.length}</strong>
              <span> left in this slate</span>
            </p>
          )}
          <Link className="btn ghost btn-sm" to="/watchlist">
            Watchlist
          </Link>
        </div>
      </header>

      {error && !current && (
        <div className="fy-empty" role="alert">
          <p className="eyebrow">Couldn’t load picks</p>
          <h2>Something went wrong</h2>
          <p className="lede form-error" style={{ margin: 0 }}>
            {error}
          </p>
          <div className="fy-empty-actions">
            <button
              type="button"
              className="btn primary"
              onClick={() => setReloadToken((n) => n + 1)}
            >
              Try again
            </button>
            <Link className="btn ghost" to="/search">
              Browse search
            </Link>
          </div>
        </div>
      )}

      {error && current && (
        <p className="form-error fy-error" role="alert">
          {error}
        </p>
      )}

      {!error && !current && (
        <div className="fy-empty" role="status">
          <p className="eyebrow">Slate clear</p>
          <h2>No more picks right now</h2>
          <p className="lede">
            Rate more titles in Search or History, or refresh later as your taste
            evolves.
          </p>
          <div className="fy-empty-actions">
            <Link className="btn primary" to="/search">
              Browse search
            </Link>
            <Link className="btn ghost" to="/history">
              Review history
            </Link>
          </div>
        </div>
      )}

      {current && (
        <article
          key={`${current.title.id}-${cardKey}`}
          className={`fy-focus ${exiting ? "fy-focus-exit" : "fy-focus-enter"}`}
          aria-labelledby="fy-current-title"
        >
          <Link
            to={`/titles/${current.title.id}`}
            className="fy-poster-link"
            aria-label={`Open details for ${current.title.name}${
              yearOf(current.title) ? `, ${yearOf(current.title)}` : ""
            }`}
          >
            <div className="fy-poster-frame">
              {poster ? (
                <img
                  className="fy-poster"
                  src={poster}
                  alt=""
                  draggable={false}
                  decoding="async"
                  fetchPriority="high"
                />
              ) : (
                <div className="fy-poster fy-poster-fallback" aria-hidden="true">
                  <span className="fy-fallback-letter">
                    {current.title.name.slice(0, 1)}
                  </span>
                  <span className="fy-fallback-name">{current.title.name}</span>
                </div>
              )}
              {(current.reasons.some((r) => r.code === "hidden_gem") ||
                current.reasons.some((r) => r.code === "discovery")) && (
                <div className="fy-badges">
                  {current.reasons.some((r) => r.code === "hidden_gem") && (
                    <span className="rec-badge gem">Hidden gem</span>
                  )}
                  {current.reasons.some((r) => r.code === "discovery") && (
                    <span className="rec-badge discovery">Discovery</span>
                  )}
                </div>
              )}
            </div>
          </Link>

          <div className="fy-meta-block">
            <h2
              ref={titleRef}
              id="fy-current-title"
              className="fy-title"
              tabIndex={-1}
            >
              {current.title.name}
            </h2>
            <p className="fy-meta">
              {yearOf(current.title) && <span>{yearOf(current.title)}</span>}
              {current.title.media_type && (
                <span className="ob-pill">{current.title.media_type}</span>
              )}
              {current.title.vote_average > 0 && (
                <span className="ob-score">
                  <span className="sr-only">Rating </span>★{" "}
                  {current.title.vote_average.toFixed(1)}
                </span>
              )}
              {current.title.genres.length > 0 && (
                <span className="fy-genres">
                  {current.title.genres
                    .slice(0, 3)
                    .map((g) => g.name)
                    .join(" · ")}
                </span>
              )}
            </p>

            {current.reasons.length > 0 && (
              <div className="fy-why">
                <p className="why-label" id="fy-why-label">
                  Why this pick
                </p>
                <ul className="reasons fy-reasons" aria-labelledby="fy-why-label">
                  {current.reasons.slice(0, 3).map((r, idx) => (
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
              className="fy-actions"
              role="group"
              aria-label={`Actions for ${current.title.name}`}
            >
              <button
                type="button"
                className="fy-act fy-act-pass"
                disabled={busy}
                aria-label={`Pass on ${current.title.name}`}
                onClick={() => void act("dislike")}
              >
                <span className="fy-act-label">Pass</span>
                <span className="fy-act-hint">Not for me</span>
              </button>
              <button
                type="button"
                className="fy-act fy-act-save"
                disabled={busy}
                aria-label={`Save ${current.title.name} to watchlist`}
                onClick={() => void act("watchlist")}
              >
                <span className="fy-act-label">Save</span>
                <span className="fy-act-hint">Watch later</span>
              </button>
              <button
                type="button"
                className="fy-act fy-act-like"
                disabled={busy}
                aria-label={`Like ${current.title.name}`}
                onClick={() => void act("like")}
              >
                <span className="fy-act-label">Like</span>
                <span className="fy-act-hint">More like this</span>
              </button>
            </div>

            <p className="fy-detail-hint">
              <Link to={`/titles/${current.title.id}`}>Full details</Link>
              {remaining > 0 && (
                <span className="fy-remaining">
                  {" "}
                  · {remaining} more in queue
                </span>
              )}
            </p>
          </div>
        </article>
      )}

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
