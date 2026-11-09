import { useCallback, useEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { getGuestRecommendations } from "../api/guest";
import type { OnboardingReaction, RecommendationItem } from "../api/titles";
import type { FeedbackAction } from "../components/ActionToast";
import { ContrastToggle } from "../components/ContrastToggle";
import { PickActions } from "../components/PickActions";
import { PickCarousel } from "../components/PickCarousel";
import { SiteFooter } from "../components/SiteFooter";
import { loadGuestDraft, saveGuestDraft } from "../features/guest/guestDraft";
import { isRating } from "../features/onboarding/constants";
import { POST_WATCH_RATINGS } from "../features/taste/ratingScale";
import { OnboardingDeckView } from "./OnboardingPage";

type Phase = "deck" | "results";

/**
 * Try CineTaste without an account: rate a few films, get a slate of picks to
 * choose from tonight. Nothing is stored server-side; the answers live in this
 * browser and follow the guest into onboarding if they sign up.
 */
export function GuestPage() {
  const [phase, setPhase] = useState<Phase>("deck");
  const [items, setItems] = useState<RecommendationItem[]>([]);
  const [ratings, setRatings] = useState(0);
  const [savedCount, setSavedCount] = useState(() => loadGuestDraft().saved.length);
  const [leaving, setLeaving] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const fetchPicks = useCallback(async (reactions: OnboardingReaction[]) => {
    const saved = new Set(loadGuestDraft().saved);
    const data = await getGuestRecommendations(reactions, 20);
    // Saved picks are not answers, so the server may offer them again.
    setItems(data.items.filter((i) => !saved.has(i.title.id)));
    setRatings(reactions.filter((r) => isRating(r.action)).length);
  }, []);

  const onGuestFinish = useCallback(
    async (reactions: OnboardingReaction[]) => {
      await fetchPicks(reactions);
      setPhase("results");
    },
    [fetchPicks],
  );

  useEffect(() => {
    if (phase === "results") headingRef.current?.focus();
  }, [phase]);

  const remove = useCallback(async (titleId: string) => {
    setLeaving((prev) => new Set(prev).add(titleId));
    await new Promise((r) => setTimeout(r, 180));
    setItems((prev) => prev.filter((i) => i.title.id !== titleId));
    setLeaving((prev) => {
      const next = new Set(prev);
      next.delete(titleId);
      return next;
    });
  }, []);

  const save = useCallback(
    (item: RecommendationItem) => {
      const draft = loadGuestDraft();
      saveGuestDraft({ reactions: draft.reactions, saved: [...draft.saved, item.title.id] });
      setSavedCount(loadGuestDraft().saved.length);
      void remove(item.title.id);
    },
    [remove],
  );

  const pass = useCallback(
    (item: RecommendationItem) => {
      const draft = loadGuestDraft();
      saveGuestDraft({
        reactions: [...draft.reactions, { title_id: item.title.id, action: "not_interested" }],
        saved: draft.saved,
      });
      void remove(item.title.id);
    },
    [remove],
  );

  /**
   * "Seen it" on a pick: a guest who already knows a film can rate it instead
   * of choosing between "Want to watch" and "Not for me" — the only two
   * options before, so a film someone loved could only be recorded as a
   * rejection, and that rejection followed them into their account.
   */
  const rate = useCallback(
    (item: RecommendationItem, action: OnboardingReaction["action"]) => {
      const draft = loadGuestDraft();
      saveGuestDraft({
        reactions: [...draft.reactions, { title_id: item.title.id, action }],
        saved: draft.saved.filter((id) => id !== item.title.id),
      });
      void remove(item.title.id);
    },
    [remove],
  );

  const onPickAction = useCallback(
    (item: RecommendationItem, event: FeedbackAction) => {
      if (event === "watchlist") return save(item);
      if (event === "not_interested") return pass(item);
      const rating = POST_WATCH_RATINGS.find((option) => option.event === event);
      if (rating) rate(item, rating.event as OnboardingReaction["action"]);
    },
    [save, pass, rate],
  );

  const morePicks = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      await fetchPicks(loadGuestDraft().reactions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load more picks");
    } finally {
      setRefreshing(false);
    }
  }, [fetchPicks]);

  return (
    <div className="landing guest">
      <Helmet>
        <title>Try CineTaste — picks for tonight, no sign-up</title>
      </Helmet>

      <a className="skip-link" href="#guest-main">
        Skip to main content
      </a>

      <header className="landing-topbar">
        <div className="landing-topbar-inner">
          <Link to="/" className="brand landing-brand" aria-label="CineTaste home">
            <span className="brand-mark" aria-hidden="true">C</span>
            <div className="brand-text">
              <div className="brand-name">CineTaste</div>
              <div className="brand-tag">Guest mode · nothing saved</div>
            </div>
          </Link>
          <div className="landing-topbar-actions">
            <span className="topbar-contrast">
              <ContrastToggle compact />
            </span>
            <Link className="btn ghost btn-sm" to="/login">
              Sign in
            </Link>
            <Link className="btn primary btn-sm" to="/register">
              Create account
            </Link>
          </div>
        </div>
      </header>

      <main id="guest-main" className="guest-main" tabIndex={-1}>
        {phase === "deck" ? (
          <OnboardingDeckView options={{ mode: "guest", onGuestFinish }} />
        ) : (
          <section className="fy-stage" aria-labelledby="guest-results-heading">
            <header className="fy-header">
              <div className="fy-header-text">
                <p className="eyebrow">Picks for tonight</p>
                <h1 id="guest-results-heading" ref={headingRef} tabIndex={-1}>
                  Here&rsquo;s what we&rsquo;d watch
                </h1>
                <p className="fy-sub">
                  From your {ratings} rating{ratings === 1 ? "" : "s"}. Swipe
                  through, save the ones you want — every pick says why.
                </p>
              </div>
              <div className="fy-header-meta">
                <button
                  type="button"
                  className="btn ghost btn-sm"
                  onClick={() => setPhase("deck")}
                >
                  Rate more for sharper picks
                </button>
              </div>
            </header>

            {error && (
              <p className="form-error fy-error" role="alert">
                {error}
              </p>
            )}

            {items.length > 0 ? (
              <PickCarousel
                items={items}
                label="Your picks"
                leavingIds={leaving}
                renderActions={(item) => (
                  <PickActions item={item} onAct={onPickAction} shortcuts={false} />
                )}
              />
            ) : (
              <div className="fy-empty" role="status">
                <h2>That&rsquo;s the whole slate</h2>
                <p className="lede">
                  Your passes already count — the next slate leaves them out.
                </p>
                <div className="fy-empty-actions">
                  <button
                    type="button"
                    className="btn primary"
                    onClick={() => void morePicks()}
                    disabled={refreshing}
                  >
                    {refreshing ? "Finding more…" : "More picks"}
                  </button>
                  <Link className="btn ghost" to="/register">
                    Create free account
                  </Link>
                </div>
              </div>
            )}

            <aside className="guest-keep" aria-label="Keep your picks">
              <p>
                {savedCount > 0 ? (
                  <>
                    <strong>{savedCount} saved.</strong> Create a free account to
                    keep them in a watchlist and your taste for next time.
                  </>
                ) : (
                  <>
                    Nothing here is saved. A free account keeps your taste and
                    makes every slate sharper than the last.
                  </>
                )}
              </p>
              <Link className="btn primary btn-sm" to="/register">
                Keep my picks
              </Link>
            </aside>
          </section>
        )}
      </main>
      <SiteFooter />
    </div>
  );
}
