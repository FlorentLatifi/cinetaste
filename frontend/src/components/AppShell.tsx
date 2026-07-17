import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { ContrastToggle } from "./ContrastToggle";

const PRIMARY_NAV = [
  { to: "/", label: "For You", end: true },
  { to: "/search", label: "Search", end: false },
  { to: "/watchlist", label: "Watchlist", end: false },
  { to: "/history", label: "History", end: false },
  { to: "/account", label: "Account", end: false },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <Link
            to="/"
            className="brand"
            style={{ textDecoration: "none", color: "inherit" }}
            aria-label="CineTaste home"
          >
            <span className="brand-mark" aria-hidden="true">
              C
            </span>
            <div className="brand-text">
              <div className="brand-name">CineTaste</div>
              <div className="brand-tag">Taste-first discovery</div>
            </div>
          </Link>
          <nav className="topbar-nav" aria-label="Main">
            {PRIMARY_NAV.map((item) => (
              <NavLink
                key={item.to}
                className="nav-link"
                to={item.to}
                end={item.end}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            <ContrastToggle compact />
            <span className="user-chip" title={user?.email || undefined}>
              <span className="sr-only">Signed in as </span>
              {user?.display_name || user?.email}
            </span>
            <button
              type="button"
              className="btn ghost btn-sm"
              onClick={() => void logout()}
              aria-label="Sign out of CineTaste"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main id="main-content" className="main" tabIndex={-1}>
        {children}
      </main>
      <nav className="bottom-nav" aria-label="Primary">
        {PRIMARY_NAV.map((item) => (
          <NavLink
            key={item.to}
            className="bottom-nav-link"
            to={item.to}
            end={item.end}
          >
            <span className="bottom-nav-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
