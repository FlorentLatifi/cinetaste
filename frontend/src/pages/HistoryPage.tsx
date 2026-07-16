import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { HistoryItem } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

const FILTERS: { id: string; label: string; state?: string }[] = [
  { id: "all", label: "All" },
  { id: "like", label: "Liked", state: "like" },
  { id: "rated", label: "Rated", state: "rated" },
  { id: "watched", label: "Watched", state: "watched" },
  { id: "watchlist", label: "Watchlist", state: "watchlist" },
  { id: "dislike", label: "Passed", state: "dislike" },
  { id: "not_interested", label: "Not interested", state: "not_interested" },
];

function formatWhen(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

function resolveFilter(raw: string | null): (typeof FILTERS)[number] {
  const found = FILTERS.find((f) => f.id === raw);
  return found ?? FILTERS[0];
}

export function HistoryPage() {
  const { accessToken } = useAuth();
  const [params, setParams] = useSearchParams();
  const filter = useMemo(
    () => resolveFilter(params.get("state")),
    [params],
  );
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const data = await titlesApi.getHistory(accessToken, {
          state: filter.state,
        });
        if (!cancelled) setItems(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load history");
          setItems([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, filter.state]);

  function setFilter(next: (typeof FILTERS)[number]) {
    if (next.id === "all") {
      setParams({}, { replace: true });
    } else {
      setParams({ state: next.id }, { replace: true });
    }
  }

  async function clearItem(titleId: string) {
    if (!accessToken) return;
    setBusyId(titleId);
    setError(null);
    try {
      await titlesApi.interact(accessToken, titleId, "clear");
      setItems((prev) => prev.filter((i) => i.title.id !== titleId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not clear");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="feed" aria-labelledby="history-heading">
      <div className="feed-header">
        <div>
          <p className="eyebrow">Your activity</p>
          <h1 id="history-heading">History</h1>
          <p className="lede">
            Titles you&apos;ve liked, rated, saved, passed, or marked watched.
            Clearing a row undoes that relationship for taste and For You.
          </p>
        </div>
        <Link className="btn ghost" to="/">
          For You
        </Link>
      </div>

      <div
        className="history-filters"
        role="toolbar"
        aria-label="Filter history by status"
      >
        {FILTERS.map((f) => {
          const active = f.id === filter.id;
          return (
            <button
              key={f.id}
              type="button"
              className={active ? "history-filter active" : "history-filter"}
              aria-pressed={active}
              onClick={() => setFilter(f)}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      {loading && (
        <div className="center-inline">
          <div className="spinner" aria-label="Loading" />
        </div>
      )}

      {!loading && !items.length && (
        <div className="callout" role="status">
          {filter.id === "all"
            ? "No history yet. Rate or save titles from For You or search."
            : `No titles marked “${filter.label}” yet.`}
        </div>
      )}

      {!loading && items.length > 0 && (
        <ul className="history-list" aria-label="Title history">
          {items.map((item) => {
            const name = item.title.name;
            const busy = busyId === item.title.id;
            return (
              <li key={item.title.id} className="history-row">
                <Link
                  to={`/titles/${item.title.id}`}
                  className="history-poster-link"
                  tabIndex={-1}
                  aria-hidden="true"
                >
                  <div className="history-poster" aria-hidden="true">
                    {item.title.poster_url ? (
                      <img src={item.title.poster_url} alt="" loading="lazy" />
                    ) : (
                      <div className="poster-fallback">{name.slice(0, 1)}</div>
                    )}
                  </div>
                </Link>
                <div className="history-body">
                  <h2 className="history-title">
                    <Link to={`/titles/${item.title.id}`} className="rec-title-link">
                      {name}
                    </Link>
                  </h2>
                  <p className="meta-line">
                    <span className={`history-badge state-${item.state}`}>
                      {item.label}
                    </span>
                    <span> · {formatWhen(item.updated_at)}</span>
                    {item.title.release_date
                      ? ` · ${item.title.release_date.slice(0, 4)}`
                      : ""}
                  </p>
                </div>
                <div className="history-actions">
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={busy}
                    aria-label={`Clear status for ${name}`}
                    onClick={() => void clearItem(item.title.id)}
                  >
                    {busy ? "Clearing…" : "Clear"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
