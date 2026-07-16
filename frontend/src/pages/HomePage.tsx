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
      <div className="center-inline home-loading">
        <div className="spinner" />
        <p className="ob-loading-copy">
          {welcome?.fromOnboarding
            ? "Fetching your first personal picks…"
            : "Loading For You…"}
        </p>
      </div>
    );
  }

  return (
    <section className="feed">
      {welcome?.fromOnboarding && (
        <div className="welcome-banner" role="status">
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
          <h1>
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

      {error && <p className="form-error">{error}</p>}

      {!items.length && (
        <div className="callout">
          No recommendations yet. Seed the catalog and complete onboarding with a
          few ratings.
        </div>
      )}

      <div className="rec-grid">
        {items.map((item) => (
          <article key={item.title.id} className="rec-card">
            <div className="rec-poster">
              {item.title.poster_url ? (
                <img
                  src={item.title.poster_url}
                  alt={item.title.name}
                  loading="lazy"
                />
              ) : (
                <div className="poster-fallback">{item.title.name}</div>
              )}
            </div>
            <div className="rec-body">
              <h3>{item.title.name}</h3>
              <p className="meta-line">
                {item.title.media_type.toUpperCase()}
                {item.title.release_date
                  ? ` · ${item.title.release_date.slice(0, 4)}`
                  : ""}
                {` · ★ ${item.title.vote_average.toFixed(1)}`}
              </p>
              {item.reasons.length > 0 && (
                <div className="why-block">
                  <p className="why-label">Why this pick</p>
                  <ul className="reasons">
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
              <div className="rec-actions">
                <button
                  type="button"
                  className="btn ghost"
                  disabled={busyId === item.title.id}
                  onClick={() => void act(item.title.id, "dislike")}
                >
                  Pass
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={busyId === item.title.id}
                  onClick={() => void act(item.title.id, "watchlist")}
                >
                  Save
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busyId === item.title.id}
                  onClick={() => void act(item.title.id, "like")}
                >
                  Like
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
