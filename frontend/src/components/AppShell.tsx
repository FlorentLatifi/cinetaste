import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { ContrastToggle } from "./ContrastToggle";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <Link
          to="/"
          className="brand"
          style={{ textDecoration: "none", color: "inherit" }}
          aria-label="CineTaste home"
        >
          <span className="brand-mark" aria-hidden="true">
            C
          </span>
          <div>
            <div className="brand-name">CineTaste</div>
            <div className="brand-tag">Taste-first discovery</div>
          </div>
        </Link>
        <nav className="topbar-nav" aria-label="Main">
          <NavLink className="nav-link" to="/" end>
            For You
          </NavLink>
          <NavLink className="nav-link" to="/search">
            Search
          </NavLink>
          <NavLink className="nav-link" to="/watchlist">
            Watchlist
          </NavLink>
          <NavLink className="nav-link" to="/history">
            History
          </NavLink>
          <NavLink className="nav-link" to="/account">
            Account
          </NavLink>
        </nav>
        <div className="topbar-right">
          <ContrastToggle compact />
          <span className="user-chip" title={user?.email || undefined}>
            <span className="sr-only">Signed in as </span>
            {user?.display_name || user?.email}
          </span>
          <button
            type="button"
            className="btn ghost"
            onClick={() => void logout()}
            aria-label="Sign out of CineTaste"
          >
            Sign out
          </button>
        </div>
      </header>
      <main id="main-content" className="main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
