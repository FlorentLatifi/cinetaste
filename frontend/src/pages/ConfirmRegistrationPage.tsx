import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { ContrastToggle } from "../components/ContrastToggle";
import { PasswordField } from "../components/PasswordField";
import { useAuth } from "../features/auth/AuthContext";

/**
 * Where the sign-up email lands. The account is created here, and only with
 * the password chosen at sign-up: someone who signed up as another person
 * sends this link to that person, who must not be able to finish an account
 * whose password the impostor picked.
 */
export function ConfirmRegistrationPage() {
  const [params] = useSearchParams();
  const token = params.get("token")?.trim() ?? "";
  const { confirmRegistration } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<{ message: string; expired: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await confirmRegistration(token, password);
      navigate("/onboarding", { replace: true });
    } catch (err) {
      setError({
        message: err instanceof ApiError ? err.message : "Could not finish signing up.",
        expired: err instanceof ApiError && err.code === "invalid_confirmation_token",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">Almost done</p>
        <h1>Finish creating your account</h1>
        <p className="lede">
          Your email is confirmed by this link. Enter the password you chose when
          you signed up and you&rsquo;re in.
        </p>
      </div>
      {!token ? (
        <div className="auth-card">
          <h2>Link incomplete</h2>
          <p className="form-error" role="alert">
            This link is missing its token. Open the most recent email and try again.
          </p>
          <p className="auth-switch">
            <Link to="/register">Sign up again</Link>
          </p>
        </div>
      ) : (
        <form className="auth-card" onSubmit={onSubmit}>
          <h2>Confirm sign-up</h2>
          <PasswordField
            label="Password you chose"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
          {error && (
            <p className="form-error" role="alert">
              {error.message}
            </p>
          )}
          {error?.expired && (
            <p className="auth-switch">
              <Link to="/register">Sign up again</Link> to get a fresh link.
            </p>
          )}
          <button className="btn primary" type="submit" disabled={submitting}>
            {submitting ? "Creating your account…" : "Create my account"}
          </button>
          <p className="auth-switch">
            Already confirmed? <Link to="/login">Sign in</Link>
          </p>
          <div className="auth-contrast">
            <ContrastToggle compact />
          </div>
        </form>
      )}
    </div>
  );
}
