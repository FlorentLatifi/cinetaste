import { Link } from "react-router-dom";
import { ContrastToggle } from "../components/ContrastToggle";

/**
 * Written from what the code actually stores, not from a template. Every list
 * below maps to a real table or a real request:
 * `users`, `interaction_events`, `user_title_state`, `taste_profiles`,
 * `recommendation_impressions`, and the three hashed token tables.
 *
 * If the data model changes, this page changes with it.
 */
export function PrivacyPage() {
  return (
    <div className="legal-page">
      <header className="legal-head">
        <Link className="brand-back" to="/">
          CineTaste
        </Link>
        <ContrastToggle compact />
      </header>

      <main className="legal-body" id="main-content">
        <h1>Privacy</h1>
        <p className="lede">
          CineTaste is a personal project that recommends films and television. It
          keeps the minimum it needs to do that, and nothing it does not.
        </p>

        <h2>What is stored</h2>
        <dl className="legal-list">
          <dt>Your account</dt>
          <dd>
            Email address, a bcrypt hash of your password (never the password
            itself), an optional display name, and whether you have confirmed the
            address.
          </dd>

          <dt>What you tell the recommender</dt>
          <dd>
            Every rating, watchlist save, “not interested” and undo, with the
            title and the time. This log is append-only — undo records a new
            event rather than erasing the old one — because the taste profile is
            rebuilt from it.
          </dd>

          <dt>Your taste profile</dt>
          <dd>
            Derived from the above: a list of weighted signals (genres, people,
            keywords, eras) and a numeric vector. It can be rebuilt from your
            history at any time.
          </dd>

          <dt>What you were shown</dt>
          <dd>
            Which titles appeared in each For You slate, in what position, with
            what score and which explanation codes. This exists to measure
            whether the recommendations are any good.
          </dd>

          <dt>Session tokens</dt>
          <dd>
            Sign-in, password-reset and email-confirmation tokens are stored as
            hashes, so a copy of the database cannot be turned back into a
            working link.
          </dd>
        </dl>

        <h2>Cookies</h2>
        <p>
          One: <code>ct_refresh</code>, which keeps you signed in. It is
          httpOnly, scoped to the authentication routes, and cannot be read by
          JavaScript. There is no advertising or analytics cookie, and no
          third-party tracker.
        </p>
        <p>
          Your theme, contrast and streaming-region choices are kept in your
          browser’s local storage. They never reach the server.
        </p>

        <h2>Who else sees anything</h2>
        <dl className="legal-list">
          <dt>TMDb</dt>
          <dd>
            Film and television metadata comes from TMDb. Posters load directly
            from their image servers, so <strong>TMDb sees your IP address and
            which posters your browser requested</strong> — the same as any site
            that loads images from a CDN. Your account and ratings are never sent
            to them.
          </dd>

          <dt>Hosting</dt>
          <dd>
            The API and database run on Render; the web app is served by Vercel.
            Both see ordinary request metadata such as IP addresses.
          </dd>

          <dt>Error reporting</dt>
          <dd>
            When enabled, Sentry receives crash reports. It is configured not to
            attach personal data, so reports carry a request id and a stack
            trace, not your email or your history.
          </dd>

          <dt>Email</dt>
          <dd>
            Password-reset and confirmation messages go through an email
            provider, which necessarily sees the address they are sent to.
          </dd>
        </dl>
        <p>Nothing is sold, and nothing is shared for advertising.</p>

        <h2>What you can do</h2>
        <ul className="legal-list-plain">
          <li>
            <strong>Take it with you.</strong> Account → Taste → Download JSON
            exports your taste profile as a readable file.
          </li>
          <li>
            <strong>Delete everything.</strong> Account → Delete account removes
            your account, your history, your profile and your tokens. It asks for
            your password because it cannot be undone.
          </li>
          <li>
            <strong>Ask.</strong> Anything the two buttons above do not cover,
            write and ask.
          </li>
        </ul>

        <h2>How long it is kept</h2>
        <p>
          Until you delete your account. Reset and confirmation tokens expire on
          their own — an hour and 48 hours respectively — and cached
          recommendations expire within minutes.
        </p>

        <h2>Honest limitations</h2>
        <p>
          This is a portfolio project on free hosting, not a company. It has no
          dedicated security team, and at the time of writing the database has no
          automated off-site backup. Do not store anything here you would be upset
          to lose, and use a password you do not use elsewhere.
        </p>

        <h2>Contact</h2>
        <p>
          Questions, or a request this page does not cover:{" "}
          <a href="mailto:PLACEHOLDER@example.com">PLACEHOLDER@example.com</a>.
        </p>

        <p className="legal-updated">Last updated 24 September 2026.</p>

        <p>
          <Link to="/">Back to CineTaste</Link>
        </p>
      </main>
    </div>
  );
}
