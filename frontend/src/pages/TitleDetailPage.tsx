import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

function yearOf(title: Title): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}

function largePoster(title: Title): string | null {
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

export function TitleDetailPage() {
  const { titleId } = useParams<{ titleId: string }>();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const [title, setTitle] = useState<Title | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

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

  async function act(event: "like" | "dislike" | "watchlist" | "not_interested") {
    if (!accessToken || !title) return;
    setBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, title.id, event);
      navigate("/", { replace: false });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
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
      <Link className="btn ghost" to="/">
        ← For You
      </Link>

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
          {error && <p className="form-error">{error}</p>}
        </div>
      </div>
    </article>
  );
}
