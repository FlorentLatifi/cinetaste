import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

export function SearchPage() {
  const { accessToken } = useAuth();
  const [params, setParams] = useSearchParams();
  const initial = params.get("q") || "";
  const [query, setQuery] = useState(initial);
  const [results, setResults] = useState<Title[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    const q = params.get("q")?.trim() || "";
    if (!q || q.length < 2 || !accessToken) {
      setResults([]);
      setSearched(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await titlesApi.searchTitles(accessToken, q);
        if (!cancelled) {
          setResults(data);
          setSearched(true);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Search failed");
          setResults([]);
          setSearched(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params, accessToken]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2) {
      setError("Type at least 2 characters.");
      return;
    }
    setParams(q ? { q } : {});
  }

  return (
    <section className="feed">
      <div className="feed-header">
        <div>
          <p className="eyebrow">Discover</p>
          <h1>Search the catalog</h1>
          <p className="lede">Find a title you know, then rate or save it.</p>
        </div>
      </div>

      <form className="search-bar" onSubmit={onSubmit} role="search">
        <input
          type="search"
          name="q"
          placeholder="Search movies & TV…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          minLength={2}
          aria-label="Search titles"
        />
        <button className="btn primary" type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && <p className="form-error">{error}</p>}

      {searched && !loading && !results.length && !error && (
        <div className="callout">No titles matched “{params.get("q")}”.</div>
      )}

      <div className="rec-grid">
        {results.map((title) => (
          <article key={title.id} className="rec-card compact">
            <Link to={`/titles/${title.id}`} className="rec-poster-link">
              <div className="rec-poster">
                {title.poster_url ? (
                  <img src={title.poster_url} alt={title.name} loading="lazy" />
                ) : (
                  <div className="poster-fallback">{title.name}</div>
                )}
              </div>
            </Link>
            <div className="rec-body">
              <h3>
                <Link to={`/titles/${title.id}`} className="rec-title-link">
                  {title.name}
                </Link>
              </h3>
              <p className="meta-line">
                {title.media_type.toUpperCase()}
                {title.release_date ? ` · ${title.release_date.slice(0, 4)}` : ""}
                {title.vote_average ? ` · ★ ${title.vote_average.toFixed(1)}` : ""}
              </p>
              <p className="genres">
                {title.genres.map((g) => g.name).join(" · ") || "—"}
              </p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
