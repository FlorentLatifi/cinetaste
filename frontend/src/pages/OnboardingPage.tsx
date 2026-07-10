import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as titlesApi from "../api/titles";
import type { Title } from "../api/titles";
import { useAuth } from "../features/auth/AuthContext";

type Reaction = { title_id: string; action: "like" | "dislike" };

export function OnboardingPage() {
  const { accessToken, user } = useAuth();
  const navigate = useNavigate();
  const [cards, setCards] = useState<Title[]>([]);
  const [index, setIndex] = useState(0);
  const [reactions, setReactions] = useState<Reaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (user?.onboarding_completed_at) {
      navigate("/", { replace: true });
      return;
    }
    if (!accessToken) return;

    let cancelled = false;
    (async () => {
      try {
        const data = await titlesApi.getOnboardingCards(accessToken);
        if (!cancelled) setCards(data.items);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Could not load onboarding cards",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken, user, navigate]);

  const current = cards[index];
  const likes = useMemo(
    () => reactions.filter((r) => r.action === "like").length,
    [reactions],
  );

  async function react(action: "like" | "dislike") {
    if (!current) return;
    const nextReactions = [
      ...reactions.filter((r) => r.title_id !== current.id),
      { title_id: current.id, action },
    ];
    setReactions(nextReactions);

    if (index + 1 < cards.length) {
      setIndex((i) => i + 1);
      return;
    }
    await finish(nextReactions);
  }

  async function finish(finalReactions: Reaction[]) {
    if (!accessToken) return;
    setSubmitting(true);
    setError(null);
    try {
      await titlesApi.completeOnboarding(accessToken, finalReactions);
      // Force full reload of auth user state
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not finish onboarding");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" />
      </div>
    );
  }

  if (!current && !submitting) {
    return (
      <div className="center-screen narrow">
        <h1>Onboarding unavailable</h1>
        <p className="lede">{error || "No cards available. Seed the catalog first."}</p>
      </div>
    );
  }

  return (
    <div className="onboarding">
      <div className="onboarding-meta">
        <p className="eyebrow">Taste calibration</p>
        <h1>Swipe what you’d actually watch</h1>
        <p className="lede">
          Like or pass. We build a living taste profile — not a generic popularity list.
        </p>
        <div className="progress-row">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${cards.length ? ((index + 1) / cards.length) * 100 : 0}%` }}
            />
          </div>
          <span>
            {Math.min(index + 1, cards.length)}/{cards.length} · {likes} likes
          </span>
        </div>
      </div>

      {current && (
        <article className="swipe-card">
          <div className="swipe-poster">
            {current.poster_url ? (
              <img src={current.poster_url} alt={current.name} />
            ) : (
              <div className="poster-fallback">{current.name}</div>
            )}
          </div>
          <div className="swipe-body">
            <h2>{current.name}</h2>
            <p className="meta-line">
              {current.media_type.toUpperCase()}
              {current.release_date ? ` · ${current.release_date.slice(0, 4)}` : ""}
              {current.vote_average ? ` · ★ ${current.vote_average.toFixed(1)}` : ""}
            </p>
            <p className="genres">
              {current.genres.map((g) => g.name).join(" · ") || "Uncategorized"}
            </p>
            <p className="overview">{current.overview || "No synopsis available."}</p>
            <div className="swipe-actions">
              <button
                type="button"
                className="btn danger"
                disabled={submitting}
                onClick={() => void react("dislike")}
              >
                Pass
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={submitting}
                onClick={() => void react("like")}
              >
                Like
              </button>
            </div>
            {index >= 8 && likes >= 1 && (
              <button
                type="button"
                className="btn ghost full"
                disabled={submitting}
                onClick={() => void finish(reactions)}
              >
                {submitting ? "Building your profile…" : "Finish early with current likes"}
              </button>
            )}
            {error && <p className="form-error">{error}</p>}
          </div>
        </article>
      )}
    </div>
  );
}
