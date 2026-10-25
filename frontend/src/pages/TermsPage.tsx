import { Link } from "react-router-dom";
import { ContrastToggle } from "../components/ContrastToggle";
import { CONTACT_EMAIL, ISSUES_URL } from "../lib/contact";

/**
 * Plain-language terms for a small, free service. Written to match what the
 * product actually does (see the privacy page for what is stored); have it
 * reviewed before relying on it commercially.
 */
export function TermsPage() {
  return (
    <div className="legal-page">
      <header className="legal-head">
        <Link className="brand-back" to="/">
          CineTaste
        </Link>
        <ContrastToggle compact />
      </header>

      <main className="legal-body" id="main-content">
        <h1>Terms of use</h1>
        <p className="lede">
          CineTaste recommends films and television from what you tell it you
          like. Using it means agreeing to the terms below. They are short on
          purpose.
        </p>

        <h2>The service</h2>
        <p>
          CineTaste is free. It suggests titles and explains why; it does not
          stream anything, sell anything, or guarantee that a recommendation will
          suit you. Film and TV information comes from TMDB, and where-to-watch
          data from JustWatch via TMDB. CineTaste is not endorsed or certified by
          either, and that information can be incomplete or out of date.
        </p>

        <h2>Your account</h2>
        <ul className="legal-list-plain">
          <li>
            You need to be at least 16, or the age of digital consent where you
            live if that is higher.
          </li>
          <li>
            Use an email address you own. When this service requires a confirmed
            address, an account is only created once you open the link sent to it.
          </li>
          <li>
            Keep your password to yourself and use one you do not use elsewhere.
            You are responsible for what happens under your account.
          </li>
          <li>
            You can delete your account at any time from Account → Delete
            account. That removes your profile, history and ratings.
          </li>
        </ul>

        <h2>Fair use</h2>
        <p>Please do not:</p>
        <ul className="legal-list-plain">
          <li>
            scrape the catalogue or the recommendations, or call the API in bulk;
          </li>
          <li>
            try to get around rate limits, sign-in protections or anything else
            that keeps the service available for everyone;
          </li>
          <li>
            open accounts under other people's email addresses, or use the
            service to harass anyone.
          </li>
        </ul>
        <p>
          Accounts that do any of this may be suspended or removed, normally after
          a warning.
        </p>

        <h2>What you can expect</h2>
        <p>
          The service is provided as it is, without any warranty. It may be slow,
          unavailable, or change how it works. If it is ever shut down, there will
          be notice on the site first, and the export in Account → Taste lets you
          keep your taste profile.
        </p>
        <p>
          To the extent the law allows, the people running CineTaste are not
          liable for indirect losses from using it — a disappointing evening
          included. Nothing here limits rights you have under the law where you
          live.
        </p>

        <h2>Changes</h2>
        <p>
          If these terms change in a way that matters, the change will be shown on
          the site before it applies. Continuing to use CineTaste after that means
          accepting the new terms; deleting your account is always an option.
        </p>

        <h2>Contact</h2>
        <p>
          Questions about these terms, or something to report:{" "}
          {CONTACT_EMAIL ? (
            <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
          ) : (
            <a href={ISSUES_URL} target="_blank" rel="noreferrer noopener">
              open an issue
            </a>
          )}
          . How your data is handled is on the <Link to="/privacy">privacy page</Link>.
        </p>

        <p className="legal-updated">Last updated 20 October 2026.</p>

        <p>
          <Link to="/">Back to CineTaste</Link>
        </p>
      </main>
    </div>
  );
}
