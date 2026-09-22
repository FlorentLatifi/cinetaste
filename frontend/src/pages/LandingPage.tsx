import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { ContrastToggle } from "../components/ContrastToggle";

const DEMO_TITLE = "The Shawshank Redemption";

export function LandingPage() {
  return (
    <div className="landing">
      <Helmet>
        <title>CineTaste — Movie & TV Recommendations That Match Your Taste</title>
        <meta name="description" content="Not another catalog browser. Rate what you know, skip the rest — CineTaste builds a living taste profile and recommends movies worth your time." />
        <meta property="og:title" content="CineTaste — Taste-First Movie Discovery" />
        <meta property="og:description" content="One poster. Your taste. Every pick explained." />
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
            <Link className="btn primary btn-sm" to="/register">Get started</Link>
          </div>
        </div>
      </header>

      <main id="landing-main" className="landing-main" tabIndex={-1}>
        <section className="landing-hero" aria-labelledby="landing-hero-title">
          <div className="landing-ambient" aria-hidden="true" />

          <p className="eyebrow landing-eyebrow">Not another catalog</p>
          <h1 id="landing-hero-title" className="landing-headline">
            One poster. Your taste. Every pick explained.
          </h1>
          <p className="landing-lede">
            Rate what you know. Skip the rest. CineTaste builds a living profile
            and surfaces movies worth your time — not what is trending this week.
          </p>

          <div className="landing-stage" aria-label="Product preview">
            <div className="landing-poster-wrap">
              <div className="landing-poster" aria-hidden="true">
                <span className="landing-poster-letter">S</span>
                <span className="landing-poster-label">{DEMO_TITLE}</span>
                <span className="landing-poster-year">1994 · ★ 9.3</span>
              </div>
            </div>

            <h2 className="landing-film-title">{DEMO_TITLE}</h2>
            <p className="landing-film-meta">
              <span className="ob-pill">movie</span>
              <span>1994</span>
              <span className="ob-score">★ 9.3</span>
              <span>Drama</span>
            </p>

            <div className="landing-why">
              <p className="why-label">Why this pick</p>
              <ul className="reasons">
                <li className="reason-primary">
                  Prison drama matches the emotional depth in your taste
                </li>
                <li>Highly rated but not a chart-topper — a hidden gem</li>
              </ul>
            </div>

            <div className="landing-demo-actions" role="group" aria-label="Demo actions — create an account to use For You">
              <span className="fy-act fy-act-fav landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">My favorite movie</span>
              </span>
              <span className="fy-act fy-act-like landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">I like it</span>
              </span>
              <span className="fy-act fy-act-nope landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">I don't like it</span>
              </span>
            </div>
            <p className="landing-demo-note">
              Preview of For You — 6 rating levels after onboarding.
            </p>
          </div>

          <div className="landing-cta">
            <Link className="btn primary landing-cta-primary" to="/register">
              Start free
            </Link>
            <Link className="btn ghost" to="/login">
              I already have an account
            </Link>
          </div>
        </section>

        <section className="landing-social" aria-labelledby="landing-social-title">
          <h2 id="landing-social-title" className="landing-social-title">
            Why people are switching
          </h2>
          <div className="landing-social-grid">
            <div className="landing-social-card">
              <span className="landing-step-num" aria-hidden="true">"</span>
              <p><strong>Finally</strong> a rec system that explains <em>why</em>. No more endless scrolling.</p>
            </div>
            <div className="landing-social-card">
              <span className="landing-step-num" aria-hidden="true">"</span>
              <p>Onboarding took 2 minutes and the picks were <strong>scarily accurate</strong>.</p>
            </div>
            <div className="landing-social-card">
              <span className="landing-step-num" aria-hidden="true">"</span>
              <p>Letterboxd for taste. I actually <strong>discovered</strong> movies I&rsquo;d never have found.</p>
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
              <h3>One pick at a time</h3>
              <p>A single poster focus — from "Favorite" to "Don't like it". No endless grid noise.</p>
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
          <Link to="/register">Create an account</Link>
          {" · "}
          <Link to="/login">Sign in</Link>
        </p>
      </footer>
    </div>
  );
}
