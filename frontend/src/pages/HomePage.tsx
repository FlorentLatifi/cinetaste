import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ActionToast } from "../components/ActionToast";
import { PickActions } from "../components/PickActions";
import { PickCarousel } from "../components/PickCarousel";
import { useForYouQueue } from "../features/for-you/useForYouQueue";

export function HomePage() {
  const {
    needsOnboarding,
    items,
    error,
    loading,
    welcome,
    toast,
    undoBusy,
    leaving,
    act,
    undoLast,
    dismissToast,
    reload,
  } = useForYouQueue();

  // A ref, not state: the shortcut listener must see the card in view at the
  // moment of the keypress, not as of the last render.
  const active = useRef(0);
  // Which card has its rating row open, so the S shortcut and the button agree.
  const [ratingFor, setRatingFor] = useState<string | null>(null);
  const onActiveChange = useCallback((i: number) => {
    active.current = i;
  }, []);

  useEffect(() => {
    if (loading || needsOnboarding || items.length === 0) return;

    function onKeyDown(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      const item = items[Math.min(active.current, items.length - 1)];
      if (!item) return;
      const key = e.key.toLowerCase();
      if (key === "w") {
        e.preventDefault();
        void act(item, "watchlist");
      } else if (key === "n") {
        e.preventDefault();
        void act(item, "not_interested");
      } else if (key === "s") {
        e.preventDefault();
        setRatingFor(item.title.id);
      } else if (key === "escape" && ratingFor) {
        setRatingFor(null);
      } else if (key === "u" && toast && !undoBusy) {
        e.preventDefault();
        void undoLast();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [items, loading, needsOnboarding, act, toast, undoBusy, undoLast, ratingFor]);

  if (needsOnboarding) {
    return (
      <section className="fy-stage fy-stage-gate">
        <p className="eyebrow">Almost there</p>
        <h1>Train your taste in under two minutes</h1>
        <p className="lede">
          Rate movies you know — skip the rest. We build an explainable profile
          and a personal For You slate, not a popularity dump.
        </p>
        <Link className="btn primary" to="/onboarding">
          Start onboarding
        </Link>
      </section>
    );
  }

  if (loading) {
    return (
      <div className="fy-stage" role="status" aria-live="polite" aria-busy="true">
        <div className="pick-skeleton" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="pick-skeleton-card">
              <div className="pick-skeleton-poster shimmer" />
              <div className="fy-skeleton-line shimmer" />
              <div className="fy-skeleton-line short shimmer" />
            </div>
          ))}
        </div>
        <p className="sr-only">
          {welcome?.fromOnboarding
            ? "Fetching your first personal picks"
            : "Loading For You"}
        </p>
      </div>
    );
  }

  return (
    <section className="fy-stage" aria-labelledby="for-you-heading">
      <header className="fy-header">
        <div className="fy-header-text">
          <p className="eyebrow">For you</p>
          <h1 id="for-you-heading">
            {welcome?.fromOnboarding
              ? "Picks matched to you"
              : "Picks matched to your taste"}
          </h1>
          {welcome?.fromOnboarding ? (
            <p className="fy-sub" role="status">
              Built from{" "}
              <strong>
                {welcome.ratingsCount ?? "your"} rating
                {(welcome.ratingsCount ?? 2) === 1 ? "" : "s"}
              </strong>
              . Swipe through them — every pick says why.
            </p>
          ) : (
            <p className="fy-sub">
              Swipe through tonight&rsquo;s picks. Save what you want to watch,
              pass on the rest — each choice sharpens the next slate.
            </p>
          )}
        </div>
        <div className="fy-header-meta">
          <Link className="btn ghost btn-sm fy-watchlist-link" to="/watchlist">
            Watchlist
          </Link>
        </div>
      </header>

      {error && items.length === 0 && (
        <div className="fy-empty" role="alert">
          <p className="eyebrow">Couldn&rsquo;t load picks</p>
          <h2>Something went wrong</h2>
          <p className="lede form-error" style={{ margin: 0 }}>
            {error}
          </p>
          <div className="fy-empty-actions">
            <button type="button" className="btn primary" onClick={reload}>
              Try again
            </button>
            <Link className="btn ghost" to="/search">
              Browse search
            </Link>
          </div>
        </div>
      )}

      {error && items.length > 0 && (
        <p className="form-error fy-error" role="alert">
          {error}
        </p>
      )}

      {!error && items.length === 0 && (
        <div className="fy-empty" role="status">
          <p className="eyebrow">Slate clear</p>
          <h2>You&rsquo;ve been through every pick</h2>
          <p className="lede">
            Everything you did here has already reshaped your taste. Get a fresh
            slate built from it.
          </p>
          <div className="fy-empty-actions">
            <button type="button" className="btn primary" onClick={reload}>
              New picks
            </button>
            <Link className="btn ghost" to="/watchlist">
              Open watchlist
            </Link>
          </div>
        </div>
      )}

      {items.length > 0 && (
        <PickCarousel
          items={items}
          label="Your picks"
          linkToDetail
          leavingIds={leaving}
          onActiveChange={onActiveChange}
          renderActions={(item) => (
            <PickActions
              item={item}
              onAct={(it, event) => {
                setRatingFor(null);
                void act(it, event);
              }}
              ratingOpen={ratingFor === item.title.id}
              onRatingOpenChange={(open) => setRatingFor(open ? item.title.id : null)}
            />
          )}
        />
      )}

      {items.length > 0 && (
        <p className="fy-keys-hint">
          <span className="sr-only">Keyboard shortcuts: </span>
          <kbd>←</kbd> <kbd>→</kbd> Browse · <kbd>W</kbd> Want to watch ·{" "}
          <kbd>S</kbd> Seen it · <kbd>N</kbd> Not for me
          {toast ? (
            <>
              {" "}· <kbd>U</kbd> Undo
            </>
          ) : null}
        </p>
      )}

      {toast && (
        <ActionToast
          message={toast.message}
          undoBusy={undoBusy}
          onUndo={() => void undoLast()}
          onDismiss={dismissToast}
        />
      )}
    </section>
  );
}
