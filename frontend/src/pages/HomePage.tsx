import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { RecommendationItem } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

export function HomePage() {
  const { accessToken, user } = useAuth();
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const needsOnboarding = !user?.onboarding_completed_at;

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
          setError(err instanceof ApiError ? err.message : "Could not load recommendations");
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
          Swipe a short set of titles. We’ll build an explainable profile and serve a personal
          For You slate — not a TMDb popularity dump.
        </p>
        <Link className="btn primary" to="/onboarding">
          Start onboarding
        </Link>
      </section>
    );
  }

  if (loading) {
    return (
      <div className="center-inline">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <section className="feed">
      <div className="feed-header">
        <div>
          <p className="eyebrow">For you</p>
          <h1>Picks matched to your taste</h1>
          <p className="lede">Every card includes why it was recommended.</p>
        </div>
        <Link className="btn ghost" to="/watchlist">
          Watchlist
        </Link>
      </div>

      {error && <p className="form-error">{error}</p>}

      {!items.length && (
        <div className="callout">
          No recommendations yet. Seed the catalog and complete onboarding with a few likes.
        </div>
      )}

      <div className="rec-grid">
        {items.map((item) => (
          <article key={item.title.id} className="rec-card">
            <div className="rec-poster">
              {item.title.poster_url ? (
                <img src={item.title.poster_url} alt={item.title.name} loading="lazy" />
              ) : (
                <div className="poster-fallback">{item.title.name}</div>
              )}
            </div>
            <div className="rec-body">
              <h3>{item.title.name}</h3>
              <p className="meta-line">
                {item.title.media_type.toUpperCase()}
                {item.title.release_date ? ` · ${item.title.release_date.slice(0, 4)}` : ""}
                {` · ★ ${item.title.vote_average.toFixed(1)}`}
              </p>
              <ul className="reasons">
                {item.reasons.map((r) => (
                  <li key={r.code + r.message}>{r.message}</li>
                ))}
              </ul>
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
