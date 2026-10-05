import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { ContrastToggle } from "../components/ContrastToggle";
import { PasswordField } from "../components/PasswordField";
import { useAuth } from "../features/auth/AuthContext";
import { hasGuestDraft, loadGuestDraft } from "../features/guest/guestDraft";

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [draft] = useState(() => loadGuestDraft());

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password, displayName || undefined);
      // Straight to the deck: there is nothing on For You until it's done.
      // If the server wants the address confirmed first, the route shows
      // "check your inbox" and continues here afterwards.
      navigate("/onboarding", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create account");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">
          <Link to="/" className="auth-brand-link">
            Start in minutes
          </Link>
        </p>
        <h1>Build a profile that gets you, not the algorithm’s average viewer.</h1>
        <p className="lede">
          Sign up, swipe a short onboarding set, and get explainable recommendations immediately.
        </p>
        {hasGuestDraft(draft) && (
          <p className="lede auth-carry" role="status">
            Your {draft.reactions.length} guest answer
            {draft.reactions.length === 1 ? "" : "s"}
            {draft.saved.length > 0
              ? ` and ${draft.saved.length} saved pick${draft.saved.length === 1 ? "" : "s"}`
              : ""}{" "}
            come with you — no need to rate them again.
          </p>
        )}
      </div>
      <form className="auth-card" onSubmit={onSubmit}>
        <h2>Create account</h2>
        <label>
          Display name
          <input
            type="text"
            autoComplete="nickname"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Optional"
          />
        </label>
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <PasswordField
          label="Password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          required
          minLength={8}
          showStrength
        />
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="btn primary" type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create account"}
        </button>
        <p className="auth-switch">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
        <div className="auth-contrast">
          <ContrastToggle compact />
        </div>
      </form>
    </div>
  );
}
