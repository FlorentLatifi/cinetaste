import { Link } from "react-router-dom";
import { ContrastToggle } from "../components/ContrastToggle";

/**
 * Public marketing home for guests — poster-first, minimal, discovery-focused.
 * Authenticated users never see this (RootRoute → For You).
 */
export function LandingPage() {
  return (
    <div className="landing">
      <a className="skip-link" href="#landing-main">
        Skip to main content
      </a>

      <header className="landing-topbar">
        <div className="landing-topbar-inner">
          <Link to="/" className="brand landing-brand" aria-label="CineTaste home">
            <span className="brand-mark" aria-hidden="true">
              C
            </span>
            <div className="brand-text">
              <div className="brand-name">CineTaste</div>
              <div className="brand-tag">Taste-first discovery</div>
            </div>
          </Link>
          <div className="landing-topbar-actions">
            <ContrastToggle compact />
            <Link className="btn ghost btn-sm" to="/login">
              Sign in
            </Link>
            <Link className="btn primary btn-sm" to="/register">
              Get started
            </Link>
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

          {/* Demo recommendation stage — mirrors For You hierarchy */}
          <div className="landing-stage" aria-label="Product preview">
            <div className="landing-poster-wrap">
              <div className="landing-poster" aria-hidden="true">
                <span className="landing-poster-letter">M</span>
                <span className="landing-poster-label">Mock Classic</span>
                <span className="landing-poster-year">2010 · ★ 7.8</span>
              </div>
            </div>

            <h2 className="landing-film-title">Mock Classic</h2>
            <p className="landing-film-meta">
              <span className="ob-pill">movie</span>
              <span>2010</span>
              <span className="ob-score">★ 7.8</span>
              <span>Drama</span>
            </p>

            <div className="landing-why">
              <p className="why-label">Why this pick</p>
              <ul className="reasons">
                <li className="reason-primary">
                  Fits the quiet drama side of your taste
                </li>
                <li>Highly rated but not a chart-topper — a hidden gem</li>
              </ul>
            </div>

            <div
              className="landing-demo-actions"
              role="group"
              aria-label="Demo actions — create an account to use For You"
            >
              <span className="fy-act fy-act-pass landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">Pass</span>
                <span className="fy-act-hint">Not for me</span>
              </span>
              <span className="fy-act fy-act-save landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">Save</span>
                <span className="fy-act-hint">Watch later</span>
              </span>
              <span className="fy-act fy-act-like landing-demo-btn" aria-hidden="true">
                <span className="fy-act-label">Like</span>
                <span className="fy-act-hint">More like this</span>
              </span>
            </div>
            <p className="landing-demo-note">
              Preview of For You — actions unlock after a short taste calibration.
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

        <section className="landing-steps" aria-labelledby="landing-how-title">
          <h2 id="landing-how-title" className="landing-section-title">
            How it works
          </h2>
          <ol className="landing-step-list">
            <li>
              <span className="landing-step-num" aria-hidden="true">
                01
              </span>
              <h3>Rate what you know</h3>
              <p>Skip unfamiliar titles. Only real signal trains your profile.</p>
            </li>
            <li>
              <span className="landing-step-num" aria-hidden="true">
                02
              </span>
              <h3>One pick at a time</h3>
              <p>A single poster focus — Pass, Save, or Like. No endless grid noise.</p>
            </li>
            <li>
              <span className="landing-step-num" aria-hidden="true">
                03
              </span>
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
