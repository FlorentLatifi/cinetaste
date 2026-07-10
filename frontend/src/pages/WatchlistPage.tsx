import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

export function WatchlistPage() {
  const { accessToken } = useAuth();
  const [items, setItems] = useState<Title[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await titlesApi.getWatchlist(accessToken);
        if (!cancelled) setItems(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load watchlist");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  return (
    <section className="feed">
      <div className="feed-header">
        <div>
          <p className="eyebrow">Saved</p>
          <h1>Watchlist</h1>
        </div>
        <Link className="btn ghost" to="/">
          Back to For you
        </Link>
      </div>
      {error && <p className="form-error">{error}</p>}
      {!items.length && <div className="callout">Nothing saved yet. Hit Save on a recommendation.</div>}
      <div className="rec-grid">
        {items.map((title) => (
          <article key={title.id} className="rec-card compact">
            <div className="rec-poster">
              {title.poster_url ? (
                <img src={title.poster_url} alt={title.name} />
              ) : (
                <div className="poster-fallback">{title.name}</div>
              )}
            </div>
            <div className="rec-body">
              <h3>{title.name}</h3>
              <p className="genres">{title.genres.map((g) => g.name).join(" · ")}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
