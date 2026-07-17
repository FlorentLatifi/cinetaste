import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import * as authApi from "../api/auth";
import type { TasteSummary } from "../api/auth";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { PasswordField } from "../components/PasswordField";
import { useAuth } from "../features/auth/AuthContext";
import {
  parseTasteSnapshot,
  type TasteSnapshotV1,
} from "../features/taste/snapshot";
import {
  useColorScheme,
  type ColorSchemePreference,
} from "../features/theme/colorScheme";
import { useContrast } from "../features/theme/contrast";

type AccountTab = "profile" | "taste" | "appearance" | "danger";

const TABS: { id: AccountTab; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "taste", label: "Taste" },
  { id: "appearance", label: "Appearance" },
  { id: "danger", label: "Danger zone" },
];

function parseTab(raw: string | null): AccountTab {
  if (raw === "profile" || raw === "taste" || raw === "appearance" || raw === "danger") {
    return raw;
  }
  return "profile";
}

export function AccountPage() {
  const { user, accessToken, logout } = useAuth();
  const { isHigh, setContrast } = useContrast();
  const { preference: colorPref, setPreference: setColorPref } = useColorScheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const tablistId = useId();

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
  const [confirmMerge, setConfirmMerge] = useState(false);
  const [confirmClearImport, setConfirmClearImport] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function setTab(next: AccountTab) {
    setSearchParams(
      next === "profile" ? {} : { tab: next },
      { replace: true },
    );
  }

  function onTabListKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const idx = TABS.findIndex((t) => t.id === tab);
    if (idx < 0) return;
    let nextIdx = idx;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      nextIdx = (idx + 1) % TABS.length;
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      nextIdx = (idx - 1 + TABS.length) % TABS.length;
    } else if (e.key === "Home") {
      e.preventDefault();
      nextIdx = 0;
    } else if (e.key === "End") {
      e.preventDefault();
      nextIdx = TABS.length - 1;
    } else {
      return;
    }
    const next = TABS[nextIdx];
    setTab(next.id);
    requestAnimationFrame(() => {
      document.getElementById(`account-tab-${next.id}`)?.focus();
    });
  }

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
      setConfirmMerge(false);
    } catch (err) {
      setImportError(
        err instanceof ApiError ? err.message : "Could not merge snapshot",
      );
      setConfirmMerge(false);
    } finally {
      setImportBusy(false);
    }
  }

  async function clearImportOverlay() {
    if (!accessToken) return;
    setImportBusy(true);
    setImportError(null);
    try {
      const summary = await authApi.clearTasteImport(accessToken);
      setTaste(summary);
      setExportStatus("Cleared merged snapshot overlay. Profile uses live ratings only.");
      setConfirmClearImport(false);
    } catch (err) {
      setImportError(
        err instanceof ApiError ? err.message : "Could not clear import overlay",
      );
      setConfirmClearImport(false);
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
        Profile details, taste signals, display preferences, and account security —
        one section at a time.
      </p>

      <div
        className="account-tabs"
        role="tablist"
        aria-label="Account sections"
        id={tablistId}
        onKeyDown={onTabListKeyDown}
      >
        {TABS.map((t) => {
          const selected = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`account-tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`account-panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              className={`account-tab${selected ? " active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "profile" && (
        <div
          className="account-card"
          role="tabpanel"
          id="account-panel-profile"
          aria-labelledby="account-tab-profile"
        >
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
            <button
              type="button"
              className="btn ghost"
              onClick={() => setTab("taste")}
            >
              View taste
            </button>
          </div>
        </div>
      )}

      {tab === "taste" && (
        <div
          className="account-card"
          role="tabpanel"
          id="account-panel-taste"
          aria-labelledby="account-tab-taste"
        >
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
                {taste.has_import_overlay
                  ? ` · ${taste.import_overlay_count ?? 0} imported`
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
              {taste.has_import_overlay && (
                <div className="taste-export">
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={importBusy}
                    onClick={() => setConfirmClearImport(true)}
                  >
                    Clear imported snapshot
                  </button>
                </div>
              )}
            </>
          )}

          <div
            className="taste-export"
            role="group"
            aria-label="Export or open taste snapshot"
          >
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
            <div
              className="taste-import-preview"
              aria-label="Imported snapshot preview"
            >
              <h3 className="taste-subhead">Snapshot preview</h3>
              <p className="meta-line">
                Exported {importPreview.exported_at.slice(0, 10)}
                {" · "}v{importPreview.profile_version}
                {" · "}
                {importPreview.likes.length} likes /{" "}
                {importPreview.dislikes.length} dislikes
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
                  onClick={() => setConfirmMerge(true)}
                >
                  Merge into my profile
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={importBusy}
                  onClick={() => {
                    setImportPreview(null);
                    setImportError(null);
                    setConfirmMerge(false);
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
      )}

      {tab === "appearance" && (
        <div
          className="account-card"
          role="tabpanel"
          id="account-panel-appearance"
          aria-labelledby="account-tab-appearance"
        >
          <h2>Display</h2>
          <p className="lede" style={{ margin: 0 }}>
            Theme and contrast are saved on this device. System theme follows your
            OS light/dark preference.
          </p>
          <label className="contrast-choice">
            <span>Theme</span>
            <select
              value={colorPref}
              onChange={(e) =>
                setColorPref(e.target.value as ColorSchemePreference)
              }
              aria-label="Color theme"
            >
              <option value="system">System</option>
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </label>
          <label className="contrast-choice">
            <span>Contrast</span>
            <select
              value={isHigh ? "high" : "normal"}
              onChange={(e) =>
                setContrast(e.target.value === "high" ? "high" : "normal")
              }
              aria-label="Contrast mode"
            >
              <option value="normal">Standard</option>
              <option value="high">High contrast</option>
            </select>
          </label>
        </div>
      )}

      {tab === "danger" && (
        <form
          className="account-card danger-zone"
          role="tabpanel"
          id="account-panel-danger"
          aria-labelledby="account-tab-danger"
          onSubmit={onDelete}
        >
          <h2>Delete account</h2>
          <p className="lede" style={{ margin: 0 }}>
            This cannot be undone. Enter your password and type{" "}
            <strong>DELETE</strong> to confirm. Deletion removes taste data,
            watchlist, and history.
          </p>
          <PasswordField
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
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
      )}

      <ConfirmDialog
        open={confirmMerge}
        title="Merge taste snapshot?"
        description="This soft-blends the file’s likes and dislikes into your profile. Live ratings still dominate. You can clear the import later."
        confirmLabel="Merge"
        cancelLabel="Cancel"
        busy={importBusy}
        onConfirm={() => void mergeSnapshot()}
        onCancel={() => {
          if (!importBusy) setConfirmMerge(false);
        }}
      />

      <ConfirmDialog
        open={confirmClearImport}
        title="Clear imported snapshot?"
        description="Removes merged snapshot signals. Your live ratings and history stay; For You will recompute without the import overlay."
        confirmLabel="Clear import"
        cancelLabel="Cancel"
        busy={importBusy}
        onConfirm={() => void clearImportOverlay()}
        onCancel={() => {
          if (!importBusy) setConfirmClearImport(false);
        }}
      />
    </section>
  );
}
