import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import * as authApi from "../api/auth";
import type { TasteSummary } from "../api/auth";
import { useAuth } from "../features/auth/AuthContext";
import {
  parseTasteSnapshot,
  type TasteSnapshotV1,
} from "../features/taste/snapshot";
import { useContrast } from "../features/theme/contrast";

export function AccountPage() {
  const { user, accessToken, logout } = useAuth();
  const { isHigh, setContrast } = useContrast();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [taste, setTaste] = useState<TasteSummary | null>(null);
  const [tasteError, setTasteError] = useState<string | null>(null);
  const [tasteLoading, setTasteLoading] = useState(true);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [importPreview, setImportPreview] = useState<TasteSnapshotV1 | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await authApi.getTasteSummary(accessToken);
        if (!cancelled) setTaste(data);
      } catch (err) {
        if (!cancelled) {
          setTasteError(
            err instanceof ApiError ? err.message : "Could not load taste profile",
          );
        }
      } finally {
        if (!cancelled) setTasteLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  async function downloadTasteJson() {
    if (!accessToken) return;
    setExportBusy(true);
    setExportStatus(null);
    try {
      const data = await authApi.exportTaste(accessToken);
      const { text: _text, ...jsonBody } = data;
      const blob = new Blob([JSON.stringify(jsonBody, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = data.exported_at.slice(0, 10);
      a.href = url;
      a.download = `cinetaste-taste-${stamp}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setExportStatus("JSON downloaded.");
    } catch (err) {
      setExportStatus(
        err instanceof ApiError ? err.message : "Could not export taste profile",
      );
    } finally {
      setExportBusy(false);
    }
  }

  async function copyTasteText() {
    if (!accessToken) return;
    setExportBusy(true);
    setExportStatus(null);
    try {
      const data = await authApi.exportTaste(accessToken);
      await navigator.clipboard.writeText(data.text);
      setExportStatus("Share text copied to clipboard.");
    } catch (err) {
      setExportStatus(
        err instanceof ApiError ? err.message : "Could not copy taste profile",
      );
    } finally {
      setExportBusy(false);
    }
  }

  async function onImportFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImportError(null);
    setImportPreview(null);
    try {
      const text = await file.text();
      const parsed = parseTasteSnapshot(text);
      if (!parsed.ok) {
        setImportError(parsed.error);
        return;
      }
      setImportPreview(parsed.data);
      setExportStatus(`Opened snapshot from ${file.name}.`);
    } catch {
      setImportError("Could not read that file.");
    }
  }

  async function mergeSnapshot() {
    if (!accessToken || !importPreview) return;
    setImportBusy(true);
    setImportError(null);
    try {
      const result = await authApi.importTaste(accessToken, {
        schema: importPreview.schema,
        likes: importPreview.likes,
        dislikes: importPreview.dislikes,
      });
      setTaste(result.summary);
      setExportStatus(
        `Merged ${result.merged_features} signals into your profile (v${result.profile_version}).`,
      );
      setImportPreview(null);
    } catch (err) {
      setImportError(
        err instanceof ApiError ? err.message : "Could not merge snapshot",
      );
    } finally {
      setImportBusy(false);
    }
  }

  async function onDelete(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (confirm.trim().toUpperCase() !== "DELETE") {
      setError("Type DELETE in the confirmation field.");
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
        <div className="account-card-actions">
          <Link className="btn ghost" to="/">
            Back to For You
          </Link>
          <Link className="btn ghost" to="/history">
            History
          </Link>
        </div>
      </div>

      <div className="account-card" aria-labelledby="taste-heading">
        <h2 id="taste-heading">Your taste</h2>
        {tasteLoading && (
          <p className="meta-line" role="status">
            Loading taste profile…
          </p>
        )}
        {tasteError && (
          <p className="form-error" role="alert">
            {tasteError}
          </p>
        )}
        {!tasteLoading && taste && !taste.ready && (
          <p className="lede" style={{ margin: 0 }}>
            Not enough signal yet. Finish onboarding or rate a few titles — we
            build an explainable profile from what you like and avoid.
          </p>
        )}
        {!tasteLoading && taste?.ready && (
          <>
            <p className="meta-line">
              Profile v{taste.version}
              {taste.has_vector ? " · vector ready" : ""}
              {taste.feature_count
                ? ` · ${taste.feature_count} signals`
                : ""}
              {taste.anchor_count
                ? ` · ${taste.anchor_count} “because you liked” anchors`
                : ""}
            </p>
            {taste.likes.length > 0 && (
              <div className="taste-block">
                <h3 className="taste-subhead">You lean toward</h3>
                <ul className="taste-chips" aria-label="Positive taste signals">
                  {taste.likes.map((f) => (
                    <li key={f.key} className="taste-chip positive">
                      {f.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {taste.dislikes.length > 0 && (
              <div className="taste-block">
                <h3 className="taste-subhead">You tend to avoid</h3>
                <ul className="taste-chips" aria-label="Negative taste signals">
                  {taste.dislikes.map((f) => (
                    <li key={f.key} className="taste-chip negative">
                      {f.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!taste.likes.length && !taste.dislikes.length && (
              <p className="lede" style={{ margin: 0 }}>
                Dense taste vector is ready, but sparse features are thin. Keep
                rating to unlock clearer labels.
              </p>
            )}
          </>
        )}

        <div className="taste-export" role="group" aria-label="Export or open taste snapshot">
          <button
            type="button"
            className="btn ghost"
            disabled={exportBusy || !taste?.ready}
            onClick={() => void downloadTasteJson()}
          >
            {exportBusy ? "Working…" : "Download JSON"}
          </button>
          <button
            type="button"
            className="btn ghost"
            disabled={exportBusy || !taste?.ready}
            onClick={() => void copyTasteText()}
          >
            Copy share text
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => fileInputRef.current?.click()}
          >
            Open snapshot…
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="sr-only"
            aria-label="Open taste snapshot JSON file"
            onChange={(ev) => void onImportFile(ev)}
          />
        </div>
        {exportStatus && (
          <p className="meta-line" role="status" aria-live="polite">
            {exportStatus}
          </p>
        )}
        {importError && (
          <p className="form-error" role="alert">
            {importError}
          </p>
        )}
        {importPreview && (
          <div className="taste-import-preview" aria-label="Imported snapshot preview">
            <h3 className="taste-subhead">Snapshot preview</h3>
            <p className="meta-line">
              Exported {importPreview.exported_at.slice(0, 10)}
              {" · "}v{importPreview.profile_version}
              {" · "}
              {importPreview.likes.length} likes / {importPreview.dislikes.length}{" "}
              dislikes
            </p>
            {importPreview.likes.length > 0 && (
              <div className="taste-block">
                <p className="taste-subhead">Lean toward (file)</p>
                <ul className="taste-chips">
                  {importPreview.likes.map((f) => (
                    <li key={`imp-l-${f.key}`} className="taste-chip positive">
                      {f.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {importPreview.dislikes.length > 0 && (
              <div className="taste-block">
                <p className="taste-subhead">Tend to avoid (file)</p>
                <ul className="taste-chips">
                  {importPreview.dislikes.map((f) => (
                    <li key={`imp-d-${f.key}`} className="taste-chip negative">
                      {f.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {importPreview.anchors.length > 0 && (
              <p className="meta-line">
                Anchors:{" "}
                {importPreview.anchors
                  .map((a) => (a.year ? `${a.name} (${a.year})` : a.name))
                  .join(" · ")}
              </p>
            )}
            <p className="taste-export-note">
              Merge soft-blends these signals into your profile (scaled so live
              ratings still dominate). It does not replace history or the dense
              vector. For You cache is refreshed after merge.
            </p>
            <div className="taste-export">
              <button
                type="button"
                className="btn primary"
                disabled={importBusy}
                onClick={() => void mergeSnapshot()}
              >
                {importBusy ? "Merging…" : "Merge into my profile"}
              </button>
              <button
                type="button"
                className="btn ghost"
                disabled={importBusy}
                onClick={() => {
                  setImportPreview(null);
                  setImportError(null);
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
        <p className="taste-export-note">
          Export is private to you — no embedding vector, only readable signals
          and title anchors. Open a downloaded JSON to preview, then optionally
          merge.
        </p>
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
