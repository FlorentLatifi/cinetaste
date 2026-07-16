import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { ContrastToggle } from "../components/ContrastToggle";
import { useAuth } from "../features/auth/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const passwordResetOk = Boolean(
    (location.state as { passwordReset?: boolean } | null)?.passwordReset,
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">CineTaste</p>
        <h1>Movies & TV that match how you actually watch.</h1>
        <p className="lede">
          Not another catalog browser. A living taste profile that explains every pick.
        </p>
      </div>
      <form className="auth-card" onSubmit={onSubmit}>
        <h2>Welcome back</h2>
        {passwordResetOk && (
          <p className="callout" style={{ margin: 0 }}>
            Password updated. Sign in with your new password.
          </p>
        )}
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
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="btn primary" type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        <p className="auth-switch">
          <Link to="/forgot-password">Forgot password?</Link>
        </p>
        <p className="auth-switch">
          New here? <Link to="/register">Create an account</Link>
        </p>
        <div className="auth-contrast">
          <ContrastToggle compact />
        </div>
      </form>
    </div>
  );
}
