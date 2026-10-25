import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Credit, ProviderOffer, Title, TitleDetail, WhereToWatch } from "../api/titles";
import {
  ActionToast,
  ACTION_TOAST_MS,
  FEEDBACK_ACTION_LABELS,
  type FeedbackAction,
} from "../components/ActionToast";
import { DetailSkeleton } from "../components/CatalogSkeleton";
import { LoadFailed } from "../components/LoadFailed";
import { useAuth } from "../features/auth/AuthContext";
import { POST_WATCH_RATINGS, ratingLabel } from "../features/taste/ratingScale";
import { heroPosterUrl, posterSrcSet } from "../lib/poster";

const WATCH_REGIONS = [
  { code: "US", label: "United States" },
  { code: "GB", label: "United Kingdom" },
  { code: "CA", label: "Canada" },
  { code: "AU", label: "Australia" },
  { code: "DE", label: "Germany" },
  { code: "FR", label: "France" },
  { code: "ES", label: "Spain" },
  { code: "IT", label: "Italy" },
  { code: "BR", label: "Brazil" },
  { code: "IN", label: "India" },
  { code: "JP", label: "Japan" },
] as const;

const REGION_STORAGE_KEY = "ct_watch_region";

function loadWatchRegion(): string {
  try {
    const stored = localStorage.getItem(REGION_STORAGE_KEY);
    if (stored && /^[A-Z]{2}$/.test(stored)) return stored;
  } catch {
    // ignore
  }
  return "US";
}

function ProviderRow({
  label,
  offers,
}: {
  label: string;
  offers: ProviderOffer[];
}) {
  if (!offers.length) return null;
  return (
    <div className="watch-row">
      <h3 className="watch-row-label">{label}</h3>
      <ul className="watch-provider-list">
        {offers.map((p) => (
          <li key={`${label}-${p.provider_id}`} className="watch-provider">
            {p.logo_url ? (
              <img src={p.logo_url} alt="" className="watch-logo" loading="lazy" />
            ) : (
              <span className="watch-logo fallback" aria-hidden="true">
                {p.name.slice(0, 1)}
              </span>
            )}
            <span className="watch-name">{p.name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function yearOf(title: TitleDetail): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
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

type ViewerStatus = { state: string | null; rating: string | null };
const NO_STATUS: ViewerStatus = { state: null, rating: null };

/** What the API will report after this action (mirrors taste_signals). */
function statusAfter(event: FeedbackAction | "clear"): ViewerStatus {
  switch (event) {
    case "clear":
      return NO_STATUS;
    case "rate_1":
      return { state: "dislike", rating: event };
    case "rate_3":
    case "rate_4":
      return { state: "like", rating: event };
    case "rate_2":
    case "mid":
      return { state: "rated", rating: event };
    case "watchlist":
    case "not_interested":
    case "watched":
      return { state: event, rating: null };
    default:
      return { state: event, rating: null };
  }
}

/** The single event that recreates a status, for Undo. */
function eventFor(status: ViewerStatus): FeedbackAction | null {
  if (status.rating) return status.rating as FeedbackAction;
  switch (status.state) {
    case "watchlist":
    case "not_interested":
    case "watched":
    case "like":
    case "dislike":
      return status.state;
    default:
      return null;
  }
}

/** "Loved it", "It was ok"… for a rated title, or null. */
function ratedLabel(status: ViewerStatus): string | null {
  if (status.rating) return ratingLabel(status.rating as FeedbackAction) ?? null;
  if (status.state === "watched") return "no rating";
  return null;
}

export function TitleDetailPage() {
  const { titleId } = useParams<{ titleId: string }>();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const [title, setTitle] = useState<TitleDetail | null>(null);
  const [similar, setSimilar] = useState<Title[]>([]);
  const [watch, setWatch] = useState<WhereToWatch | null>(null);
  const [watchRegion, setWatchRegion] = useState(loadWatchRegion);
  const [watchLoading, setWatchLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadFailure, setLoadFailure] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // What the viewer has already done with this title, as the API reports it.
  const [status, setStatus] = useState<ViewerStatus>(NO_STATUS);
  // The status before the last action, so Undo can put it back exactly.
  const [undoTo, setUndoTo] = useState<ViewerStatus | null>(null);
  const [rateOpen, setRateOpen] = useState(false);
  const [toast, setToast] = useState<{ action: FeedbackAction; message: string } | null>(
    null,
  );
  const [undoBusy, setUndoBusy] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rateFirstRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!accessToken || !titleId) return;
    let cancelled = false;
    setLoading(true);
    setSimilar([]);
    setWatch(null);
    setStatus(NO_STATUS);
    setUndoTo(null);
    setRateOpen(false);
    setToast(null);
    (async () => {
      try {
        const data = await titlesApi.getTitle(accessToken, titleId);
        if (cancelled) return;
        setTitle(data);
        setStatus({ state: data.my_state ?? null, rating: data.my_rating ?? null });
        try {
          const sims = await titlesApi.getSimilarTitles(accessToken, titleId, 10);
          if (!cancelled) setSimilar(sims);
        } catch {
          if (!cancelled) setSimilar([]);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadFailure(err instanceof ApiError ? err : null);
          setError(err instanceof ApiError ? err.message : "Could not load title");
          setTitle(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, titleId, reloadKey]);

  useEffect(() => {
    if (!accessToken || !titleId) return;
    let cancelled = false;
    setWatchLoading(true);
    (async () => {
      try {
        const data = await titlesApi.getWhereToWatch(
          accessToken,
          titleId,
          watchRegion,
        );
        if (!cancelled) setWatch(data);
      } catch {
        if (!cancelled) setWatch(null);
      } finally {
        if (!cancelled) setWatchLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, titleId, watchRegion]);

  function onRegionChange(code: string) {
    const next = code.toUpperCase();
    setWatchRegion(next);
    try {
      localStorage.setItem(REGION_STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }

  const people = useMemo(
    () => splitCredits(title?.credits || []),
    [title?.credits],
  );

  function dismissToast() {
    if (toastTimer.current) {
      clearTimeout(toastTimer.current);
      toastTimer.current = null;
    }
    setToast(null);
  }

  function showToast(action: FeedbackAction, name: string) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({
      action,
      message: `${FEEDBACK_ACTION_LABELS[action]} · ${name}`,
    });
    toastTimer.current = setTimeout(() => {
      setToast(null);
      toastTimer.current = null;
    }, ACTION_TOAST_MS);
  }

  async function act(event: FeedbackAction | "clear") {
    if (!accessToken || !title || busy) return;
    setBusy(true);
    setError(null);
    const before = status;
    try {
      await titlesApi.interact(accessToken, title.id, event);
      setStatus(statusAfter(event));
      setRateOpen(false);
      if (event === "clear") {
        setUndoTo(null);
        dismissToast();
      } else {
        setUndoTo(before);
        showToast(event, title.name);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  function openRatePanel() {
    setRateOpen(true);
    setError(null);
    requestAnimationFrame(() => rateFirstRef.current?.focus());
  }

  /** Put back exactly what was there before the last action. */
  async function undoLast() {
    if (!accessToken || !title || !undoTo || undoBusy) return;
    setUndoBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, title.id, "clear");
      const restore = eventFor(undoTo);
      if (restore) await titlesApi.interact(accessToken, title.id, restore);
      setStatus(undoTo);
      setUndoTo(null);
      setRateOpen(false);
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
    }
  }

  if (loading) {
    return <DetailSkeleton label="Loading title details" />;
  }

  if (!title) {
    // A 404 means the title is not in the catalogue and retrying cannot help.
    // Anything else — a timeout, a 500, a dropped connection — is worth
    // another attempt, and "Title not found" would be a lie about it.
    if (loadFailure && loadFailure.status !== 404) {
      return (
        <LoadFailed
          what="this title"
          message={loadFailure.message}
          onRetry={() => setReloadKey((n) => n + 1)}
        >
          <Link className="btn ghost" to="/">
            Back to For You
          </Link>
        </LoadFailed>
      );
    }
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

  const poster = heroPosterUrl(title);
  const posterSet = posterSrcSet(title);

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
        <div
          className="title-detail-poster"
          style={{ viewTransitionName: "title-poster" } as CSSProperties}
        >
          {poster ? (
            <img
              src={poster}
              srcSet={posterSet ?? undefined}
              sizes={
                posterSet
                  ? "(max-width: 720px) 34vw, 360px"
                  : undefined
              }
              alt={title.name}
              decoding="async"
              fetchPriority="high"
            />
          ) : (
            <div className="poster-fallback">{title.name}</div>
          )}
        </div>
        {/* Separate from the body so a phone can set it beside a small poster
            and keep the actions on the first screen. */}
        <div className="title-detail-head">
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
        </div>
        <div className="title-detail-body">
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

          <div
            className="rec-actions detail-actions"
            role="group"
            aria-label={`Actions for ${title.name}`}
          >
            <button
              type="button"
              className={`btn ${status.state === "watchlist" ? "primary" : "ghost"}`}
              disabled={busy}
              aria-pressed={status.state === "watchlist"}
              aria-label={
                status.state === "watchlist"
                  ? `In your watchlist — remove ${title.name}`
                  : `Want to watch — ${title.name}`
              }
              onClick={() => void act(status.state === "watchlist" ? "clear" : "watchlist")}
            >
              {status.state === "watchlist" ? "✓ In your watchlist" : "＋ Want to watch"}
            </button>
            <button
              type="button"
              className={`btn ${ratedLabel(status) ? "primary" : "ghost"}`}
              disabled={busy}
              aria-expanded={rateOpen}
              aria-controls="detail-rate-panel"
              aria-label={`Seen it — rate ${title.name}`}
              onClick={() => (rateOpen ? setRateOpen(false) : openRatePanel())}
            >
              {ratedLabel(status) ? `Seen it · ${ratedLabel(status)}` : "Seen it"}
            </button>
            <button
              type="button"
              className={`btn ${status.state === "not_interested" ? "primary" : "ghost"}`}
              disabled={busy}
              aria-pressed={status.state === "not_interested"}
              aria-label={
                status.state === "not_interested"
                  ? `Marked not for you — undo for ${title.name}`
                  : `Not for me — ${title.name}`
              }
              onClick={() =>
                void act(status.state === "not_interested" ? "clear" : "not_interested")
              }
            >
              {status.state === "not_interested" ? "✓ Not for you" : "Not for me"}
            </button>
          </div>

          {rateOpen && (
            <div
              id="detail-rate-panel"
              className="detail-rate-panel"
              role="group"
              aria-label={`Rate ${title.name}`}
            >
              <p className="detail-rate-lead">How was it?</p>
              <div className="detail-rate-row">
                {POST_WATCH_RATINGS.map((opt, i) => (
                  <button
                    key={opt.event}
                    ref={i === 0 ? rateFirstRef : undefined}
                    type="button"
                    className={`btn detail-rate-btn rate-${opt.event}`}
                    disabled={busy}
                    aria-pressed={status.rating === opt.event}
                    aria-label={`Rate ${title.name}: ${opt.label}`}
                    onClick={() => void act(opt.event)}
                  >
                    <span aria-hidden="true">{opt.emoji}</span> {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="text-btn detail-rate-skip"
                disabled={busy}
                onClick={() => void act("watched")}
              >
                Seen it — skip rating
              </button>
            </div>
          )}

          {undoTo && toast && (
            <p className="toast-ok detail-action-status" role="status">
              {FEEDBACK_ACTION_LABELS[toast.action]}.{" "}
              <Link to="/">Back to For You</Link>
              {" · "}
              <button
                type="button"
                className="text-btn"
                disabled={undoBusy}
                onClick={() => void undoLast()}
              >
                {undoBusy ? "Undoing…" : "Undo"}
              </button>
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}

          {title.keywords.length > 0 && (
            <div className="chip-row">
              {title.keywords.slice(0, 12).map((k) => (
                <span key={k} className="chip">
                  {k}
                </span>
              ))}
            </div>
          )}

          <section className="watch-section" aria-labelledby="watch-heading">
            <div className="watch-header">
              <h2 id="watch-heading">Where to watch</h2>
              <label className="watch-region">
                <span className="sr-only">Region</span>
                <select
                  value={watchRegion}
                  onChange={(e) => onRegionChange(e.target.value)}
                  aria-label="Watch region"
                >
                  {WATCH_REGIONS.map((r) => (
                    <option key={r.code} value={r.code}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {watchLoading && (
              <p className="meta-line" role="status">
                Checking availability…
              </p>
            )}
            {!watchLoading && watch && watch.available && (
              <>
                <ProviderRow label="Stream" offers={watch.flatrate} />
                <ProviderRow label="Free" offers={watch.free} />
                <ProviderRow label="With ads" offers={watch.ads} />
                <ProviderRow label="Rent" offers={watch.rent} />
                <ProviderRow label="Buy" offers={watch.buy} />
                {watch.link && (
                  <p className="watch-more">
                    <a href={watch.link} target="_blank" rel="noreferrer">
                      More options on TMDb
                    </a>
                  </p>
                )}
                <p className="watch-attribution">{watch.attribution}</p>
              </>
            )}
            {!watchLoading && watch && !watch.available && (
              <>
                <p className="meta-line">
                  No streaming or purchase options listed for {watch.region}.
                </p>
                <p className="watch-attribution">{watch.attribution}</p>
              </>
            )}
            {!watchLoading && !watch && (
              <p className="meta-line">
                Availability unavailable right now (check TMDb configuration).
              </p>
            )}
          </section>
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

      {similar.length > 0 && (
        <section className="similar-section" aria-labelledby="similar-heading">
          <h2 id="similar-heading">More like this</h2>
          <ul className="similar-row similar-row-list">
            {similar.map((t) => (
              <li key={t.id}>
                <Link to={`/titles/${t.id}`} className="similar-card">
                  <div className="similar-poster" aria-hidden="true">
                    {t.poster_url ? (
                      <img src={t.poster_url} alt="" loading="lazy" />
                    ) : (
                      <div className="poster-fallback">{t.name}</div>
                    )}
                  </div>
                  <div className="similar-name">{t.name}</div>
                  <div className="similar-meta">
                    {t.release_date ? t.release_date.slice(0, 4) : t.media_type}
                    {t.vote_average ? ` · ★ ${t.vote_average.toFixed(1)}` : ""}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {toast && (
        <ActionToast
          message={toast.message}
          undoBusy={undoBusy}
          onUndo={() => void undoLast()}
          onDismiss={dismissToast}
        />
      )}
    </article>
  );
}
