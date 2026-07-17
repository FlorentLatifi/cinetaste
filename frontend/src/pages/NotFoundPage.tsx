import { Link } from "react-router-dom";
import { ContrastToggle } from "../components/ContrastToggle";
import { useAuth } from "../features/auth/AuthContext";

/**
 * Real 404 — unknown paths no longer silently redirect home.
 */
export function NotFoundPage() {
  const { user, loading } = useAuth();

  return (
    <div className="not-found">
      <a className="skip-link" href="#not-found-main">
        Skip to main content
      </a>
      <main id="not-found-main" className="not-found-main" tabIndex={-1}>
        <p className="eyebrow">404</p>
        <h1>This page doesn’t exist</h1>
        <p className="lede">
          The link may be broken, or the title moved. Head back to discovery —
          or sign in if you were looking for your slate.
        </p>
        <div className="not-found-actions">
          {!loading && user ? (
            <>
              <Link className="btn primary" to="/">
                Back to For You
              </Link>
              <Link className="btn ghost" to="/search">
                Search
              </Link>
            </>
          ) : (
            <>
              <Link className="btn primary" to="/">
                Home
              </Link>
              <Link className="btn ghost" to="/login">
                Sign in
              </Link>
            </>
          )}
        </div>
        <div className="not-found-contrast">
          <ContrastToggle compact />
        </div>
      </main>
    </div>
  );
}
