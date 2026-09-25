import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as authApi from "../api/auth";
import { ApiError } from "../api/client";
import { ContrastToggle } from "../components/ContrastToggle";
import { useAuth } from "../features/auth/AuthContext";
import { hasGuestDraft, loadGuestDraft } from "../features/guest/guestDraft";

/**
 * Shown instead of the product while the server requires a confirmed address
 * and this one isn't. The account exists, but nothing else happens until the
 * link in the email is opened — so an address someone does not own cannot be
 * used for more than an unconfirmed placeholder.
 */
export function VerifyPendingPage() {
  const { user, accessToken, refreshUser, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [draft] = useState(() => loadGuestDraft());

  // The link usually opens in another tab or on the phone. Coming back to
  // this tab is the moment to look again, so nobody has to reload by hand.
  useEffect(() => {
    function onFocus() {
      void refreshUser().catch(() => undefined);
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshUser]);

  async function resend() {
    if (!accessToken) return;
    setBusy(true);
    setStatus(null);
    try {
      const res = await authApi.resendVerification(accessToken);
      setStatus(
        res.dev_verification_token
          ? `Dev link: /verify-email?token=${res.dev_verification_token}`
          : res.message,
      );
    } catch (err) {
      setStatus(err instanceof ApiError ? err.message : "Could not send a new link.");
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    setChecking(true);
    setStatus(null);
    try {
      await refreshUser();
      // Still here after the refresh means it is still unconfirmed.
      setStatus("Not confirmed yet. Open the link in the email, then try again.");
    } catch {
      setStatus("Could not check right now. Try again in a moment.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="auth-layout">
      <div className="auth-aside">
        <p className="eyebrow">One last step</p>
        <h1>Check your inbox</h1>
        <p className="lede">
          We sent a confirmation link to <strong>{user?.email}</strong>. Open it
          and you&rsquo;re in — this page moves on by itself.
        </p>
        {hasGuestDraft(draft) && (
          <p className="lede">
            Your {draft.reactions.length} rating
            {draft.reactions.length === 1 ? "" : "s"}
            {draft.saved.length > 0
              ? ` and ${draft.saved.length} saved pick${draft.saved.length === 1 ? "" : "s"}`
              : ""}{" "}
            from guest mode are waiting in this browser.
          </p>
        )}
      </div>
      <div className="auth-card">
        <h2>Confirm your email</h2>
        <p className="lede">
          Can&rsquo;t find it? Check spam, or send a fresh link. Older links stop
          working when a new one is sent.
        </p>
        <button className="btn primary" type="button" onClick={() => void check()} disabled={checking}>
          {checking ? "Checking…" : "I’ve confirmed it"}
        </button>
        <button className="btn ghost" type="button" onClick={() => void resend()} disabled={busy}>
          {busy ? "Sending…" : "Send a new link"}
        </button>
        {status && (
          <p className="meta-line" role="status" aria-live="polite">
            {status}
          </p>
        )}
        <p className="auth-switch">
          Wrong address?{" "}
          <Link to="/account">Delete this account</Link> and register again, or{" "}
          <button type="button" className="link-button" onClick={() => void logout()}>
            sign out
          </button>
          .
        </p>
        <div className="auth-contrast">
          <ContrastToggle compact />
        </div>
      </div>
    </div>
  );
}
