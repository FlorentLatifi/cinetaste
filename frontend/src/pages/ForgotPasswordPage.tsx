import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import * as authApi from "../api/auth";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [devToken, setDevToken] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await authApi.forgotPassword(email);
      setDone(true);
      if (res.dev_reset_token) setDevToken(res.dev_reset_token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">Account recovery</p>
        <h1>Reset your password</h1>
        <p className="lede">
          We&apos;ll issue a one-time link if an account exists for that email.
          For privacy we never say whether the address was found.
        </p>
      </div>
      <form className="auth-card" onSubmit={onSubmit}>
        <h2>Forgot password</h2>
        {done ? (
          <>
            <p className="lede" style={{ margin: 0 }}>
              If an account exists for <strong>{email}</strong>, reset
              instructions have been issued.
            </p>
            {devToken && (
              <p className="callout" style={{ margin: 0, fontSize: "0.9rem" }}>
                <strong>Dev only:</strong>{" "}
                <Link to={`/reset-password?token=${encodeURIComponent(devToken)}`}>
                  Open reset link
                </Link>
              </p>
            )}
            <Link className="btn primary" to="/login">
              Back to sign in
            </Link>
          </>
        ) : (
          <>
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
            {error && <p className="form-error">{error}</p>}
            <button className="btn primary" type="submit" disabled={submitting}>
              {submitting ? "Sending…" : "Send reset link"}
            </button>
            <p className="auth-switch">
              <Link to="/login">Back to sign in</Link>
            </p>
          </>
        )}
      </form>
    </div>
  );
}
