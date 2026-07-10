import type { ReactNode } from "react";
import { useAuth } from "../features/auth/AuthContext";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">C</span>
          <div>
            <div className="brand-name">CineTaste</div>
            <div className="brand-tag">Taste-first discovery</div>
          </div>
        </div>
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
