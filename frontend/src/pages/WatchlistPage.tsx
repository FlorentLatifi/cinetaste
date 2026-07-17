import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { CatalogSkeleton } from "../components/CatalogSkeleton";
import { PosterCard } from "../components/PosterCard";
import { useAuth } from "../features/auth/AuthContext";

export function WatchlistPage() {
  const { accessToken } = useAuth();
  const [items, setItems] = useState<Title[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  return (
    <section className="feed" aria-labelledby="watchlist-heading">
      <div className="feed-header">
        <div>
          <p className="eyebrow">Saved</p>
          <h1 id="watchlist-heading">Watchlist</h1>
          <p className="lede">Titles you want to see — cover art first.</p>
        </div>
        <Link className="btn ghost" to="/">
          Back to For You
        </Link>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <CatalogSkeleton count={8} label="Loading watchlist" />
      )}
      {!loading && !items.length && (
        <div className="callout" role="status">
          Nothing saved yet. Hit Save on a recommendation or title page.
        </div>
      )}
      {!loading && (
        <ul className="poster-grid catalog" aria-label="Watchlist">
          {items.map((title) => (
            <li key={title.id}>
              <PosterCard
                title={title}
                compact
                badge={<span className="rec-badge discovery">Saved</span>}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
