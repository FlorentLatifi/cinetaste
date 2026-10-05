import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as authApi from "../api/auth";
import { ApiError } from "../api/client";
import { ContrastToggle } from "../components/ContrastToggle";
import { useAuth } from "../features/auth/AuthContext";
import { broadcastSession } from "../features/auth/sessionChannel";

type State =
  | { status: "working" }
  | { status: "done"; email: string }
  | { status: "failed"; message: string };

/**
 * Landing page for the link in the verification email.
 *
 * Reachable signed in or out on purpose: people open mail on a different
 * device from the one they registered on, and the link itself is the proof.
 */
export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token")?.trim() ?? "";
  const { user, refreshUser } = useAuth();
  const [state, setState] = useState<State>({ status: "working" });
  // React 18+ runs effects twice in development; one token, one attempt, or the
  // second call reports the first one's success as "already used".
  const attempted = useRef(false);

  useEffect(() => {
    if (attempted.current) return;
    attempted.current = true;

    if (!token) {
      setState({
        status: "failed",
        message: "That link is missing its token. Open the most recent email and try again.",
      });
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const verified = await authApi.verifyEmail(token);
        if (cancelled) return;
        setState({ status: "done", email: verified.email });
        // Refresh the cached user so the account page stops prompting. Safe to
        // call unconditionally: with no refresh cookie it returns early, and
        // reading `user` here would capture whatever the bootstrap had not
        // finished loading yet.
        await refreshUser().catch(() => undefined);
        // The tab that registered is usually still open on "check your
        // inbox"; tell it, so it moves on without the person reloading.
        broadcastSession("signed-in");
      } catch (err) {
        if (cancelled) return;
        setState({
          status: "failed",
          message:
            err instanceof ApiError
              ? err.message
              : "Could not confirm this address. Try requesting a new link.",
        });
      }
    })();

    return () => {
      cancelled = true;
    };
    // `attempted` guards against a re-run; the token is the only real input.
  }, [token, refreshUser]);

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">Account</p>
        <h1>Confirming your email</h1>
        <p className="lede">
          This link works once and expires. Confirming keeps your account recoverable.
        </p>
      </div>

      <div className="auth-card">
        {state.status === "working" && (
          <p className="lede" role="status" aria-live="polite">
            Checking your link…
          </p>
        )}

        {state.status === "done" && (
          <>
            <h2>Email confirmed</h2>
            <p className="lede" role="status" aria-live="polite">
              {state.email} is verified.
            </p>
            <div className="account-card-actions">
              <Link className="btn" to={user ? "/" : "/login"}>
                {user ? "Back to For You" : "Sign in"}
              </Link>
            </div>
          </>
        )}

        {state.status === "failed" && (
          <>
            <h2>That link did not work</h2>
            <p className="form-error" role="alert">
              {state.message}
            </p>
            <div className="account-card-actions">
              <Link className="btn ghost" to={user ? "/account" : "/login"}>
                {user ? "Request a new link" : "Sign in"}
              </Link>
            </div>
          </>
        )}

        <div className="auth-contrast">
          <ContrastToggle compact />
        </div>
      </div>
    </div>
  );
}
