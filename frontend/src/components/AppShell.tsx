import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand" style={{ textDecoration: "none", color: "inherit" }}>
          <span className="brand-mark">C</span>
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
          <NavLink className="nav-link" to="/account">
            Account
          </NavLink>
        </nav>
        <div className="topbar-right">
          <span className="user-chip">{user?.display_name || user?.email}</span>
          <button type="button" className="btn ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
  );
}
