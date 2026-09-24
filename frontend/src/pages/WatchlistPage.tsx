import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { CatalogSkeleton } from "../components/CatalogSkeleton";
import { LoadFailed } from "../components/LoadFailed";
import { PosterCard } from "../components/PosterCard";
import { useAuth } from "../features/auth/AuthContext";

export function WatchlistPage() {
  const { accessToken } = useAuth();
  const [items, setItems] = useState<Title[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    // Clear the previous failure before retrying, or the error block outlives
    // the request that succeeded.
    setError(null);
    setLoading(true);
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
  }, [accessToken, reloadKey]);

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
      {error && !loading && (
        <LoadFailed
          what="your watchlist"
          message={error}
          onRetry={() => setReloadKey((n) => n + 1)}
        >
          <Link className="btn ghost" to="/search">
            Browse search
          </Link>
        </LoadFailed>
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
