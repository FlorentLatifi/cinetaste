import { Link } from "react-router-dom";

/**
 * The attribution line is not decoration: TMDb's terms of use require both the
 * wordmark and this exact disclaimer on anything built with their API. It has
 * to be present wherever their data is, which is every signed-in page.
 *
 * The logo file is not in the repository — TMDb distribute it themselves and
 * ask that it is not modified. Drop the PNG at `public/tmdb.svg` and it appears
 * here; without it the text attribution still satisfies the requirement.
 */
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <p className="site-footer-tmdb">
        <img
          src="/tmdb.svg"
          alt=""
          aria-hidden="true"
          className="tmdb-mark"
          width={60}
          height={8}
          onError={(e) => {
            e.currentTarget.style.display = "none";
          }}
        />
        <span>
          This product uses the{" "}
          <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer noopener">
            TMDB
          </a>{" "}
          API but is not endorsed or certified by TMDB.
        </span>
      </p>
      <p className="site-footer-links">
        <Link to="/privacy">Privacy</Link>
        {" · "}
        <a
          href="https://github.com/FlorentLatifi/cinetaste"
          target="_blank"
          rel="noreferrer noopener"
        >
          Source
        </a>
      </p>
    </footer>
  );
}
