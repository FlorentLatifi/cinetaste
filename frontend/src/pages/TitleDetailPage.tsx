import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Credit, TitleDetail } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

function yearOf(title: TitleDetail): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}

function largePoster(title: TitleDetail): string | null {
  if (!title.poster_url && !title.poster_path) return null;
  const raw = title.poster_path || title.poster_url || "";
  if (raw.startsWith("http") && raw.includes("/w500")) {
    return raw.replace("/w500", "/w780");
  }
  if (raw.startsWith("/") && !raw.startsWith("http")) {
    return `https://image.tmdb.org/t/p/w780${raw}`;
  }
  return title.poster_url;
}

function splitCredits(credits: Credit[]) {
  const cast = credits.filter((c) => c.credit_type === "cast").slice(0, 12);
  const crew = credits.filter((c) => c.credit_type === "crew");
  const directors = crew.filter((c) => (c.job || "").toLowerCase() === "director");
  const writers = crew.filter((c) =>
    ["writer", "screenplay", "creator"].includes((c.job || "").toLowerCase()),
  );
  return { cast, directors, writers };
}

export function TitleDetailPage() {
  const { titleId } = useParams<{ titleId: string }>();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const [title, setTitle] = useState<TitleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken || !titleId) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await titlesApi.getTitle(accessToken, titleId);
        if (!cancelled) setTitle(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load title");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, titleId]);

  const people = useMemo(
    () => splitCredits(title?.credits || []),
    [title?.credits],
  );

  async function act(event: "like" | "dislike" | "watchlist" | "not_interested") {
    if (!accessToken || !title) return;
    setBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, title.id, event);
      if (event === "watchlist") {
        setToast("Saved to watchlist");
        setBusy(false);
        return;
      }
      navigate("/", { replace: false });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="center-inline">
        <div className="spinner" />
      </div>
    );
  }

  if (!title) {
    return (
      <section className="account-page">
        <h1>Title not found</h1>
        <p className="lede">{error || "This title is not in the catalog."}</p>
        <Link className="btn primary" to="/">
          Back to For You
        </Link>
      </section>
    );
  }

  const poster = largePoster(title);

  return (
    <article className="title-detail">
      <div className="title-detail-nav">
        <button type="button" className="btn ghost" onClick={() => navigate(-1)}>
          ← Back
        </button>
        <Link className="btn ghost" to="/">
          For You
        </Link>
        <Link className="btn ghost" to="/search">
          Search
        </Link>
      </div>

      <div className="title-detail-grid">
        <div className="title-detail-poster">
          {poster ? (
            <img src={poster} alt={title.name} />
          ) : (
            <div className="poster-fallback">{title.name}</div>
          )}
        </div>
        <div className="title-detail-body">
          <p className="eyebrow">{title.media_type}</p>
          <h1>{title.name}</h1>
          <p className="meta-line">
            {yearOf(title) && <span>{yearOf(title)}</span>}
            {title.runtime ? ` · ${title.runtime} min` : ""}
            {title.vote_average ? ` · ★ ${title.vote_average.toFixed(1)}` : ""}
            {title.original_language
              ? ` · ${title.original_language.toUpperCase()}`
              : ""}
          </p>
          {title.genres.length > 0 && (
            <p className="genres">
              {title.genres.map((g) => g.name).join(" · ")}
            </p>
          )}
          <p className="title-overview">
            {title.overview || "No synopsis available."}
          </p>

          {people.directors.length > 0 && (
            <p className="credit-line">
              <strong>Director</strong>{" "}
              {people.directors.map((d) => d.name).join(", ")}
            </p>
          )}
          {people.writers.length > 0 && (
            <p className="credit-line">
              <strong>Writing</strong>{" "}
              {people.writers.map((w) => w.name).join(", ")}
            </p>
          )}

          <div className="rec-actions">
            <button
              type="button"
              className="btn ghost"
              disabled={busy}
              onClick={() => void act("dislike")}
            >
              Pass
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={busy}
              onClick={() => void act("watchlist")}
            >
              Save
            </button>
            <button
              type="button"
              className="btn danger"
              disabled={busy}
              onClick={() => void act("not_interested")}
            >
              Not interested
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => void act("like")}
            >
              Like
            </button>
          </div>
          {toast && <p className="toast-ok">{toast}</p>}
          {error && <p className="form-error">{error}</p>}

          {title.keywords.length > 0 && (
            <div className="chip-row">
              {title.keywords.slice(0, 12).map((k) => (
                <span key={k} className="chip">
                  {k}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {people.cast.length > 0 && (
        <section className="cast-section">
          <h2>Cast</h2>
          <ul className="cast-grid">
            {people.cast.map((c) => (
              <li key={`${c.name}-${c.character || c.billing_order}`} className="cast-card">
                <div className="cast-avatar">
                  {c.profile_url ? (
                    <img src={c.profile_url} alt="" loading="lazy" />
                  ) : (
                    <span>{c.name.slice(0, 1)}</span>
                  )}
                </div>
                <div>
                  <div className="cast-name">{c.name}</div>
                  {c.character && <div className="cast-role">{c.character}</div>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
