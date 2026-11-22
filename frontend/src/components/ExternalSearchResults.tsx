import { useEffect, useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { ExternalTitle } from "../api/titles";

type Props = {
  accessToken: string;
  query: string;
  /** Search straight away (the catalog had nothing) rather than on request. */
  auto: boolean;
};

/**
 * "Not finding it?" — search all of TMDb and add a missing title to rate it.
 *
 * The catalog is what was ingested ahead of time, so a film someone loves can
 * simply not be there. Without this they had no way to tell us about it.
 */
export function ExternalSearchResults({ accessToken, query, auto }: Props) {
  const navigate = useNavigate();
  const headingId = useId();
  const [requested, setRequested] = useState(auto);
  const [results, setResults] = useState<ExternalTitle[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState<number | null>(null);

  // A new query starts over; an empty catalog result searches without a click.
  useEffect(() => {
    setRequested(auto);
    setResults(null);
    setError(null);
  }, [query, auto]);

  useEffect(() => {
    if (!requested) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    titlesApi
      .searchExternalTitles(accessToken, query)
      .then((rows) => {
        if (!cancelled) setResults(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 503
            ? "Searching beyond our catalog isn't available right now."
            : err instanceof ApiError
              ? err.message
              : "Could not search right now.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requested, accessToken, query]);

  async function add(item: ExternalTitle) {
    setAdding(item.tmdb_id);
    setError(null);
    try {
      const { id } = await titlesApi.importTitle(accessToken, item.media_type, item.tmdb_id);
      navigate(`/titles/${id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add that title.");
      setAdding(null);
    }
  }

  return (
    <section className="external-search" aria-labelledby={headingId}>
      <h2 id={headingId} className="search-results-heading">
        Not finding it?
      </h2>
      {!requested && (
        <p className="lede">
          Our catalog doesn&apos;t have everything yet.{" "}
          <button type="button" className="btn ghost" onClick={() => setRequested(true)}>
            Search all movies &amp; TV for “{query}”
          </button>
        </p>
      )}
      {loading && (
        <p className="meta-line" role="status">
          Searching all movies &amp; TV…
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {results && !loading && results.length === 0 && (
        <p className="callout" role="status">
          Nothing matched “{query}” anywhere. Try another spelling.
        </p>
      )}
      {results && results.length > 0 && (
        <>
          <p className="lede">Add a title to rate it — it shapes your recommendations like any other.</p>
          <ul className="poster-grid catalog" aria-label="Movies and TV from TMDb">
            {results.map((item) => (
              <li key={`${item.media_type}-${item.tmdb_id}`}>
                <article className="poster-card compact">
                  <div className="poster-card-cover">
                    <div className="poster-card-frame">
                      {item.poster_url ? (
                        <img src={item.poster_url} alt="" loading="lazy" decoding="async" />
                      ) : (
                        <div className="poster-fallback" aria-hidden="true">
                          <span className="poster-fallback-letter">{item.name.slice(0, 1)}</span>
                          <span className="poster-fallback-name">{item.name}</span>
                        </div>
                      )}
                      <div className="poster-card-shade" aria-hidden="true" />
                      <div className="poster-card-caption">
                        <h3 className="poster-card-title">{item.name}</h3>
                        <p className="poster-card-meta">
                          <span className="poster-card-type">{item.media_type}</span>
                          {item.year && <span> · {item.year}</span>}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="poster-card-body">
                    {item.title_id ? (
                      <Link
                        className="btn ghost btn-sm"
                        to={`/titles/${item.title_id}`}
                        aria-label={`Open ${item.name}${item.year ? ` (${item.year})` : ""}`}
                      >
                        Open
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className="btn primary btn-sm"
                        disabled={adding !== null}
                        // Visible text first (WCAG 2.5.3), then which title it is.
                        aria-label={`Add & rate ${item.name}${item.year ? ` (${item.year})` : ""}`}
                        onClick={() => void add(item)}
                      >
                        {adding === item.tmdb_id ? "Adding…" : "Add & rate"}
                      </button>
                    )}
                  </div>
                </article>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
