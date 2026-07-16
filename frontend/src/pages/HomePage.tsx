import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { RecommendationItem } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

type LocationState = {
  fromOnboarding?: boolean;
  ratingsCount?: number;
} | null;

export function HomePage() {
  const { accessToken, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [welcome, setWelcome] = useState<LocationState>(null);

  const needsOnboarding = !user?.onboarding_completed_at;

  useEffect(() => {
    const state = (location.state as LocationState) || null;
    if (state?.fromOnboarding) {
      setWelcome(state);
      // Clear router state so refresh doesn't re-show the banner forever
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

  async function act(
    titleId: string,
    event: "like" | "dislike" | "watchlist" | "not_interested",
  ) {
    if (!accessToken) return;
    setBusyId(titleId);
    try {
      await titlesApi.interact(accessToken, titleId, event);
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusyId(null);
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
          <p className="lede">Every card includes why it was recommended.</p>
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

      <ul className="rec-grid rec-grid-list" aria-label="Recommended titles">
        {items.map((item) => {
          const name = item.title.name;
          const busy = busyId === item.title.id;
          return (
            <li key={item.title.id} className="rec-card">
              <Link
                to={`/titles/${item.title.id}`}
                className="rec-poster-link"
                tabIndex={-1}
                aria-hidden="true"
              >
                <div className="rec-poster">
                  {item.title.poster_url ? (
                    <img
                      src={item.title.poster_url}
                      alt=""
                      loading="lazy"
                    />
                  ) : (
                    <div className="poster-fallback" aria-hidden="true">
                      {name}
                    </div>
                  )}
                </div>
              </Link>
              <div className="rec-body">
                <h3 id={`rec-title-${item.title.id}`}>
                  <Link
                    to={`/titles/${item.title.id}`}
                    className="rec-title-link"
                  >
                    {name}
                  </Link>
                </h3>
                <p className="meta-line">
                  {item.title.media_type.toUpperCase()}
                  {item.title.release_date
                    ? ` · ${item.title.release_date.slice(0, 4)}`
                    : ""}
                  {` · ★ ${item.title.vote_average.toFixed(1)}`}
                </p>
                {item.reasons.length > 0 && (
                  <div className="why-block">
                    <p className="why-label" id={`why-${item.title.id}`}>
                      Why this pick
                    </p>
                    <ul className="reasons" aria-labelledby={`why-${item.title.id}`}>
                      {item.reasons.map((r, idx) => (
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
                <div className="rec-actions" role="group" aria-label={`Actions for ${name}`}>
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={busy}
                    aria-label={`Pass on ${name}`}
                    onClick={() => void act(item.title.id, "dislike")}
                  >
                    Pass
                  </button>
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={busy}
                    aria-label={`Save ${name} to watchlist`}
                    onClick={() => void act(item.title.id, "watchlist")}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={busy}
                    aria-label={`Like ${name}`}
                    onClick={() => void act(item.title.id, "like")}
                  >
                    Like
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
