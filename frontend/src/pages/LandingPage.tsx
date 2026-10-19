import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { getGuestCards } from "../api/guest";
import type { Title } from "../api/titles";
import { SiteFooter } from "../components/SiteFooter";
import { ContrastToggle } from "../components/ContrastToggle";
import { posterSrc } from "../lib/poster";

/**
 * Real posters from the catalog, the same ones the trial starts with. The
 * page used to show a hand-drawn "S" standing in for a poster; this shows
 * what the product is actually about before anyone reads a word.
 * Decorative: it fails quietly to a plain gradient.
 */
function PosterWall() {
  const [titles, setTitles] = useState<Title[]>([]);

  useEffect(() => {
    let cancelled = false;
    getGuestCards({ limit: 18 })
      .then((data) => {
        if (!cancelled) setTitles(data.items.filter((t) => posterSrc(t)));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Too few posters looks broken rather than decorative; show nothing instead.
  if (titles.length < 6) return null;

  return (
    <div className="landing-wall" aria-hidden="true">
      <div className="landing-wall-grid">
        {titles.slice(0, 18).map((t, i) => (
          <img
            key={t.id}
            src={posterSrc(t) ?? undefined}
            alt=""
            loading={i < 6 ? "eager" : "lazy"}
            decoding="async"
            draggable={false}
          />
        ))}
      </div>
    </div>
  );
}

export function LandingPage() {
  return (
    <div className="landing">
      <Helmet>
        <title>CineTaste — Movie & TV Recommendations That Match Your Taste</title>
        <meta name="description" content="Rate a few films you know and get tonight's picks, each with the reason it was chosen. Try it without an account." />
        <meta property="og:title" content="CineTaste — Taste-First Movie Discovery" />
        <meta property="og:description" content="Rate three films. Get picks for tonight. Every pick explained." />
        <meta property="og:type" content="website" />
        <meta name="twitter:card" content="summary_large_image" />
      </Helmet>

      <a className="skip-link" href="#landing-main">
        Skip to main content
      </a>

      <header className="landing-topbar">
        <div className="landing-topbar-inner">
          <Link to="/" className="brand landing-brand" aria-label="CineTaste home">
            <span className="brand-mark" aria-hidden="true">C</span>
            <div className="brand-text">
              <div className="brand-name">CineTaste</div>
              <div className="brand-tag">Taste-first discovery</div>
            </div>
          </Link>
          <div className="landing-topbar-actions">
            <ContrastToggle compact />
            <Link className="btn ghost btn-sm" to="/login">Sign in</Link>
            <Link className="btn primary btn-sm" to="/register">Create account</Link>
          </div>
        </div>
      </header>

      <main id="landing-main" className="landing-main" tabIndex={-1}>
        <section className="landing-hero" aria-labelledby="landing-hero-title">
          <div className="landing-ambient" aria-hidden="true" />

          <p className="eyebrow landing-eyebrow">Not another catalog</p>
          <h1 id="landing-hero-title" className="landing-headline">
            Rate three films. Get tonight&rsquo;s picks. Every pick explained.
          </h1>
          <p className="landing-lede">
            Tell us a few films you know and we&rsquo;ll line up what to watch
            next — with the reason for each. No account needed to try it.
          </p>

          <div className="landing-cta landing-cta-top">
            <Link className="btn primary landing-cta-primary" to="/try">
              Try it now — no sign-up
            </Link>
            <Link className="btn ghost" to="/login">
              I already have an account
            </Link>
          </div>

          <PosterWall />

          <div className="landing-why landing-why-example">
            <p className="why-label">Every pick says why — for example</p>
            <ul className="reasons">
              <li className="reason-primary">Because you loved Arrival: same director</li>
              <li>Highly rated but not a chart-topper — a hidden gem</li>
            </ul>
          </div>

          <div className="landing-cta">
            <Link className="btn primary landing-cta-primary" to="/try">
              Start free
            </Link>
            <Link className="btn ghost" to="/register">
              Create an account
            </Link>
          </div>
        </section>

        <section className="landing-social" aria-labelledby="landing-social-title">
          <h2 id="landing-social-title" className="landing-social-title">
            What you can count on
          </h2>
          <div className="landing-social-grid">
            <div className="landing-social-card">
              <p><strong>A reason for every pick.</strong> Same director, a film you rated, a mood you like — spelled out, never a bare score.</p>
            </div>
            <div className="landing-social-card">
              <p><strong>Not just what&rsquo;s trending.</strong> Each slate mixes close matches with hidden gems and a few deliberate surprises.</p>
            </div>
            <div className="landing-social-card">
              <p><strong>Try first, sign up later.</strong> Guest ratings stay in your browser and move into your account if you create one.</p>
            </div>
          </div>
        </section>

        <section className="landing-steps" aria-labelledby="landing-how-title">
          <h2 id="landing-how-title" className="landing-section-title">
            How it works
          </h2>
          <ol className="landing-step-list">
            <li>
              <span className="landing-step-num" aria-hidden="true">01</span>
              <h3>Rate what you know</h3>
              <p>Skip unfamiliar titles. Only real signal trains your profile.</p>
            </li>
            <li>
              <span className="landing-step-num" aria-hidden="true">02</span>
              <h3>Swipe through your slate</h3>
              <p>A row of picks, not an endless grid. Save what you want to watch, pass on the rest.</p>
            </li>
            <li>
              <span className="landing-step-num" aria-hidden="true">03</span>
              <h3>Every pick explains why</h3>
              <p>Reasons you can read. Export your taste anytime from Account.</p>
            </li>
          </ol>
        </section>
      </main>

      <footer className="landing-footer">
        <p>
          CineTaste — taste-first discovery.{" "}
          <Link to="/try">Try it without an account</Link>
          {" · "}
          <Link to="/register">Create an account</Link>
          {" · "}
          <Link to="/login">Sign in</Link>
        </p>
      </footer>
      <SiteFooter />
    </div>
  );
}
