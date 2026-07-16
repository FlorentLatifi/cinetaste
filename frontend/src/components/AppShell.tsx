import type { ReactNode } from "react";
import { Link } from "react-router-dom";
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
        <div className="topbar-right">
          <Link className="btn ghost" to="/watchlist">
            Watchlist
          </Link>
          <Link className="btn ghost" to="/account">
            Account
          </Link>
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
