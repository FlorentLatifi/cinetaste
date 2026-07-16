import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as authApi from "../api/auth";
import { useAuth } from "../features/auth/AuthContext";
import { useContrast } from "../features/theme/contrast";

export function AccountPage() {
  const { user, accessToken, logout } = useAuth();
  const { isHigh, setContrast } = useContrast();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onDelete(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (confirm.trim().toUpperCase() !== "DELETE") {
      setError('Type DELETE in the confirmation field.');
      return;
    }
    if (!accessToken) {
      setError("Not signed in.");
      return;
    }
    setSubmitting(true);
    try {
      await authApi.deleteAccount(accessToken, password);
      await logout();
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete account");
      setSubmitting(false);
    }
  }

  return (
    <section className="account-page">
      <p className="eyebrow">Account</p>
      <h1>Your profile</h1>
      <p className="lede">
        Manage your CineTaste account. Deletion is permanent and removes taste
        data, watchlist, and history.
      </p>

      <div className="account-card">
        <h2>Details</h2>
        <p className="meta-line">
          <strong>Email</strong> · {user?.email}
        </p>
        {user?.display_name && (
          <p className="meta-line">
            <strong>Name</strong> · {user.display_name}
          </p>
        )}
        <p className="meta-line">
          <strong>Onboarding</strong> ·{" "}
          {user?.onboarding_completed_at ? "Complete" : "Not finished"}
        </p>
        <Link className="btn ghost" to="/">
          Back to For You
        </Link>
      </div>

      <div className="account-card">
        <h2>Display</h2>
        <p className="lede" style={{ margin: 0 }}>
          High contrast uses solid blacks, stronger borders, and brighter text.
          Your choice is saved on this device.
        </p>
        <label className="contrast-choice">
          <span>Contrast</span>
          <select
            value={isHigh ? "high" : "normal"}
            onChange={(e) => setContrast(e.target.value === "high" ? "high" : "normal")}
            aria-label="Contrast mode"
          >
            <option value="normal">Standard</option>
            <option value="high">High contrast</option>
          </select>
        </label>
      </div>

      <form className="account-card danger-zone" onSubmit={onDelete}>
        <h2>Delete account</h2>
        <p className="lede" style={{ margin: 0 }}>
          This cannot be undone. Enter your password and type{" "}
          <strong>DELETE</strong> to confirm.
        </p>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          Type DELETE to confirm
          <input
            type="text"
            autoComplete="off"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="DELETE"
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="btn danger" type="submit" disabled={submitting}>
          {submitting ? "Deleting…" : "Delete my account"}
        </button>
      </form>
    </section>
  );
}
