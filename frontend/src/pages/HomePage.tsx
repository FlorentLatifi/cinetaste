import { useAuth } from "../features/auth/AuthContext";

export function HomePage() {
  const { user } = useAuth();
  const needsOnboarding = !user?.onboarding_completed_at;

  return (
    <section className="hero-panel">
      <p className="eyebrow">Your taste profile</p>
      <h1>
        {needsOnboarding
          ? "Let’s learn what you love"
          : "Your next great watch is waiting"}
      </h1>
      <p className="lede">
        {needsOnboarding
          ? "Swipe-based onboarding is next. In a few minutes we’ll build a living taste profile — not a generic “popular” list."
          : "Personalized, explainable recommendations land here. Catalog ingest and the ranking pipeline are the next build phase."}
      </p>

      <div className="card-grid">
        <article className="info-card">
          <h3>Explainable picks</h3>
          <p>Every recommendation will ship with human reasons — themes, cast, pacing, atmosphere.</p>
        </article>
        <article className="info-card">
          <h3>Discovery, not bubbles</h3>
          <p>Relevance balanced with diversity, hidden gems, and light exploration.</p>
        </article>
        <article className="info-card">
          <h3>Gets smarter</h3>
          <p>Likes, dislikes, and watchlist signals continuously reshape your profile.</p>
        </article>
      </div>

      {needsOnboarding && (
        <div className="callout">
          <strong>Up next:</strong> interactive onboarding cards + first “For you” slate from the
          recommendation engine.
        </div>
      )}
    </section>
  );
}
