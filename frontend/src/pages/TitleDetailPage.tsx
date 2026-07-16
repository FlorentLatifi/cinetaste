import { useEffect, useMemo, useRef, useState } from "react";
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
import { useAuth } from "../features/auth/AuthContext";

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
  const [similar, setSimilar] = useState<Title[]>([]);
  const [watch, setWatch] = useState<WhereToWatch | null>(null);
  const [watchRegion, setWatchRegion] = useState(loadWatchRegion);
  const [watchLoading, setWatchLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [lastAction, setLastAction] = useState<FeedbackAction | null>(null);
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
    setLastAction(null);
    setRateOpen(false);
    setToast(null);
    (async () => {
      try {
        const data = await titlesApi.getTitle(accessToken, titleId);
        if (cancelled) return;
        setTitle(data);
        try {
          const sims = await titlesApi.getSimilarTitles(accessToken, titleId, 10);
          if (!cancelled) setSimilar(sims);
        } catch {
          if (!cancelled) setSimilar([]);
        }
      } catch (err) {
        if (!cancelled) {
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
  }, [accessToken, titleId]);

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

  async function act(event: FeedbackAction) {
    if (!accessToken || !title) return;
    setBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, title.id, event);
      setLastAction(event);
      setRateOpen(false);
      showToast(event, title.name);
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

  async function undoLast() {
    if (!accessToken || !title || !lastAction || undoBusy) return;
    setUndoBusy(true);
    setError(null);
    try {
      await titlesApi.interact(accessToken, title.id, "clear");
      setLastAction(null);
      setRateOpen(false);
      dismissToast();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo");
    } finally {
      setUndoBusy(false);
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

          <div
            className="rec-actions"
            role="group"
            aria-label={`Actions for ${title.name}`}
          >
            <button
              type="button"
              className="btn ghost"
              disabled={busy || lastAction !== null}
              aria-label={`Pass on ${title.name}`}
              onClick={() => void act("dislike")}
            >
              Pass
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={busy || lastAction !== null}
              aria-label={`Save ${title.name} to watchlist`}
              onClick={() => void act("watchlist")}
            >
              Save
            </button>
            <button
              type="button"
              className="btn danger"
              disabled={busy || lastAction !== null}
              aria-label={`Mark ${title.name} as not interested`}
              onClick={() => void act("not_interested")}
            >
              Not interested
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || lastAction !== null}
              aria-label={`Like ${title.name}`}
              onClick={() => void act("like")}
            >
              Like
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={busy || lastAction !== null}
              aria-expanded={rateOpen}
              aria-controls="detail-rate-panel"
              aria-label={`Mark ${title.name} as watched and rate it`}
              onClick={() => (rateOpen ? setRateOpen(false) : openRatePanel())}
            >
              Watched
            </button>
          </div>

          {rateOpen && !lastAction && (
            <div
              id="detail-rate-panel"
              className="detail-rate-panel"
              role="group"
              aria-label={`Rate ${title.name} after watching`}
            >
              <p className="detail-rate-lead">How was it?</p>
              <div className="detail-rate-row">
                {(
                  [
                    { event: "rate_1" as const, label: "Bad" },
                    { event: "rate_2" as const, label: "It's ok" },
                    { event: "rate_3" as const, label: "Good" },
                    { event: "rate_4" as const, label: "Favorite" },
                  ] as const
                ).map((opt, i) => (
                  <button
                    key={opt.event}
                    ref={i === 0 ? rateFirstRef : undefined}
                    type="button"
                    className={`btn detail-rate-btn rate-${opt.event}`}
                    disabled={busy}
                    aria-label={`Rate ${title.name}: ${opt.label}`}
                    onClick={() => void act(opt.event)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="text-btn detail-rate-skip"
                disabled={busy}
                onClick={() => void act("watched")}
              >
                Watched — skip rating
              </button>
            </div>
          )}

          {lastAction && (
            <p className="toast-ok detail-action-status" role="status">
              {FEEDBACK_ACTION_LABELS[lastAction]}.{" "}
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
