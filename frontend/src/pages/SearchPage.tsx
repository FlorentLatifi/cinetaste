import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { PosterCard } from "../components/PosterCard";
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
  const errorId = useId();
  const statusId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsHeadingRef = useRef<HTMLHeadingElement>(null);

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
          requestAnimationFrame(() => resultsHeadingRef.current?.focus());
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
      inputRef.current?.focus();
      return;
    }
    setParams(q ? { q } : {});
  }

  const qParam = params.get("q");
  const statusText = loading
    ? "Searching…"
    : searched
      ? results.length
        ? `${results.length} result${results.length === 1 ? "" : "s"} for “${qParam}”`
        : `No titles matched “${qParam}”`
      : "";

  return (
    <section className="feed" aria-labelledby="search-heading">
      <div className="feed-header">
        <div>
          <p className="eyebrow">Discover</p>
          <h1 id="search-heading">Search the catalog</h1>
          <p className="lede">Find a title you know — posters open the full detail.</p>
        </div>
      </div>

      <form className="search-bar" onSubmit={onSubmit} role="search" aria-label="Catalog search">
        <label className="sr-only" htmlFor="catalog-search">
          Search movies and TV
        </label>
        <input
          ref={inputRef}
          id="catalog-search"
          type="search"
          name="q"
          placeholder="Search movies & TV…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          minLength={2}
          autoComplete="off"
          enterKeyHint="search"
          aria-describedby={`${statusId}${error ? ` ${errorId}` : ""}`}
          aria-invalid={error ? true : undefined}
          aria-busy={loading || undefined}
        />
        <button className="btn primary" type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <p id={statusId} className="sr-only" aria-live="polite" aria-atomic="true">
        {statusText}
      </p>

      {error && (
        <p id={errorId} className="form-error" role="alert">
          {error}
        </p>
      )}

      {searched && !loading && (
        <h2
          ref={resultsHeadingRef}
          className="search-results-heading"
          tabIndex={-1}
        >
          {results.length
            ? `Results (${results.length})`
            : error
              ? "Search error"
              : "No results"}
        </h2>
      )}

      {searched && !loading && !results.length && !error && (
        <div className="callout" role="status">
          No titles matched “{qParam}”. Try another spelling or a shorter query.
        </div>
      )}

      <ul className="poster-grid catalog" aria-label="Search results">
        {results.map((title) => (
          <li key={title.id}>
            <PosterCard
              title={title}
              compact
              badge={
                title.genres[0] ? (
                  <span className="rec-badge discovery">{title.genres[0].name}</span>
                ) : undefined
              }
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
