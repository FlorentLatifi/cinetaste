import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import type { RecommendationItem } from "../api/titles";
import { heroPosterUrl, posterSrcSet, yearOf } from "../lib/poster";

type Props = {
  items: RecommendationItem[];
  /** Accessible name for the whole carousel. */
  label: string;
  /** Per-card controls. Receives the card's index for focus bookkeeping. */
  renderActions: (item: RecommendationItem, index: number) => ReactNode;
  /** Open the detail page from the poster and title (signed-in only). */
  linkToDetail?: boolean;
  /** Title ids animating out after an action. */
  leavingIds?: ReadonlySet<string>;
  /** Reports the card nearest the start of the track. */
  onActiveChange?: (index: number) => void;
};

/**
 * A horizontally scrolling row of picks. Native scroll-snap does the swiping,
 * so touch, trackpad and mouse all work without a gesture library, and the
 * next card peeks in from the edge so it is obvious there is more.
 *
 * Swiping only browses. Deciding is always an explicit button, because a
 * gesture that both scrolls and judges records opinions nobody meant to give.
 */
export function PickCarousel({
  items,
  label,
  renderActions,
  linkToDetail = false,
  leavingIds,
  onActiveChange,
}: Props) {
  const trackRef = useRef<HTMLOListElement>(null);
  const [active, setActive] = useState(0);
  const prevLength = useRef(items.length);
  const frame = useRef<number | null>(null);
  // Mirrors `active` for handlers that must not wait for a re-render.
  const activeRef = useRef(0);
  // While a button or arrow key is scrolling to a card, the scroll events on
  // the way there describe cards being passed, not the one chosen. Without
  // this, a keypress mid-animation could act on the wrong pick.
  const target = useRef<{ index: number; until: number } | null>(null);

  const cards = useCallback(
    () => Array.from(trackRef.current?.children ?? []) as HTMLElement[],
    [],
  );

  const nearestIndex = useCallback(() => {
    const track = trackRef.current;
    if (!track) return 0;
    const start = track.getBoundingClientRect().left;
    let best = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    cards().forEach((card, i) => {
      const distance = Math.abs(card.getBoundingClientRect().left - start);
      if (distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    });
    return best;
  }, [cards]);

  // Reported synchronously, not from an effect: the page's shortcut keys read
  // it from a native listener, which an effect can lag behind by a keypress.
  const select = useCallback(
    (index: number) => {
      activeRef.current = index;
      setActive(index);
      onActiveChange?.(index);
    },
    [onActiveChange],
  );

  const onScroll = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const nearest = nearestIndex();
      const pending = target.current;
      if (pending) {
        // Arrived, or the person grabbed the row and swiped elsewhere.
        if (nearest === pending.index || Date.now() > pending.until) {
          target.current = null;
        } else {
          return;
        }
      }
      select(nearest);
    });
  }, [nearestIndex, select]);

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  const goTo = useCallback(
    (index: number, focus = false) => {
      const list = cards();
      if (list.length === 0) return;
      const clamped = Math.max(0, Math.min(index, list.length - 1));
      const card = list[clamped];
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      card.scrollIntoView({
        behavior: reduce ? "auto" : "smooth",
        block: "nearest",
        inline: "start",
      });
      target.current = { index: clamped, until: Date.now() + 900 };
      select(clamped);
      if (focus) {
        card.querySelector<HTMLElement>(".pick-title")?.focus({ preventScroll: true });
      }
    },
    [cards, select],
  );

  // A card was acted on and removed: the one that slid into its place is the
  // natural next thing to look at, so it gets focus and the counter follows.
  useLayoutEffect(() => {
    if (items.length < prevLength.current && items.length > 0) {
      const index = Math.min(active, items.length - 1);
      cards()[index]?.querySelector<HTMLElement>(".pick-title")?.focus({ preventScroll: true });
      select(index);
    }
    prevLength.current = items.length;
  }, [items.length, active, cards, select]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select")) return;
      // From the card last chosen, not the one the animation is passing.
      if (e.key === "ArrowRight") {
        e.preventDefault();
        goTo(activeRef.current + 1, true);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        goTo(activeRef.current - 1, true);
      }
    },
    [goTo],
  );

  const activeItem = items[Math.min(active, items.length - 1)];
  const ambient = activeItem ? heroPosterUrl(activeItem.title) : null;

  return (
    <section
      className="pick-deck"
      aria-roledescription="carousel"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {ambient && (
        <div
          className="fy-ambient"
          style={{ backgroundImage: `url(${ambient})` }}
          aria-hidden
        />
      )}

      <div className="pick-controls">
        <p className="pick-position" aria-live="polite" aria-atomic="true">
          <strong>{Math.min(active + 1, items.length)}</strong>
          <span> / {items.length}</span>
        </p>
        <div className="pick-nav">
          <button
            type="button"
            className="pick-nav-btn"
            onClick={() => goTo(active - 1, true)}
            disabled={active <= 0}
            aria-label="Previous pick"
          >
            ←
          </button>
          <button
            type="button"
            className="pick-nav-btn"
            onClick={() => goTo(active + 1, true)}
            disabled={active >= items.length - 1}
            aria-label="Next pick"
          >
            →
          </button>
        </div>
      </div>

      <ol className="pick-track" ref={trackRef} onScroll={onScroll}>
        {items.map((item, index) => (
          <PickCard
            key={item.title.id}
            item={item}
            index={index}
            total={items.length}
            linkToDetail={linkToDetail}
            leaving={leavingIds?.has(item.title.id) ?? false}
            actions={renderActions(item, index)}
          />
        ))}
      </ol>

      <p className="pick-swipe-hint" aria-hidden="true">
        Swipe or use ← → to browse
      </p>
    </section>
  );
}

function PickCard({
  item,
  index,
  total,
  linkToDetail,
  leaving,
  actions,
}: {
  item: RecommendationItem;
  index: number;
  total: number;
  linkToDetail: boolean;
  leaving: boolean;
  actions: ReactNode;
}) {
  const { title, reasons } = item;
  const [posterFailed, setPosterFailed] = useState(false);
  const poster = heroPosterUrl(title);
  const srcSet = posterSrcSet(title);
  const year = yearOf(title);
  const headingId = `pick-${title.id}`;
  const gem = reasons.some((r) => r.code === "hidden_gem");
  const discovery = reasons.some((r) => r.code === "discovery");

  const frame = (
    <div className="pick-poster-frame">
      {poster && !posterFailed ? (
        <img
          className="pick-poster"
          src={poster}
          srcSet={srcSet ?? undefined}
          sizes={srcSet ? "(max-width: 640px) 72vw, 264px" : undefined}
          alt=""
          draggable={false}
          decoding="async"
          loading={index < 3 ? "eager" : "lazy"}
          fetchPriority={index === 0 ? "high" : undefined}
          onError={() => setPosterFailed(true)}
        />
      ) : (
        <div className="pick-poster pick-poster-fallback" aria-hidden="true">
          <span className="fy-fallback-letter">{title.name.slice(0, 1)}</span>
          <span className="fy-fallback-name">{title.name}</span>
        </div>
      )}
      {(gem || discovery) && (
        <div className="fy-badges">
          {gem && <span className="rec-badge gem">Hidden gem</span>}
          {discovery && <span className="rec-badge discovery">Discovery</span>}
        </div>
      )}
    </div>
  );

  return (
    <li
      className={`pick-card${leaving ? " pick-card-leaving" : ""}`}
      aria-roledescription="slide"
      aria-label={`${index + 1} of ${total}: ${title.name}`}
    >
      {linkToDetail ? (
        <Link
          to={`/titles/${title.id}`}
          className="pick-poster-link"
          viewTransition
          style={index === 0 ? ({ viewTransitionName: "title-poster" } as CSSProperties) : undefined}
          aria-label={`Open details for ${title.name}${year ? `, ${year}` : ""}`}
        >
          {frame}
        </Link>
      ) : (
        <div className="pick-poster-link">{frame}</div>
      )}

      <div className="pick-body">
        <h2 id={headingId} className="pick-title" tabIndex={-1}>
          {title.name}
        </h2>
        <p className="pick-meta">
          {year && <span>{year}</span>}
          {title.media_type && <span className="ob-pill">{title.media_type}</span>}
          {title.vote_average > 0 && (
            <span className="ob-score">
              <span className="sr-only">Rating </span>★ {title.vote_average.toFixed(1)}
            </span>
          )}
        </p>
        {title.genres.length > 0 && (
          <p className="pick-genres">
            {title.genres
              .slice(0, 3)
              .map((g) => g.name)
              .join(" · ")}
          </p>
        )}

        {reasons.length > 0 && (
          <div className="pick-why">
            <p className="why-label">Why this pick</p>
            <ul className="reasons fy-reasons">
              {reasons.slice(0, 2).map((r, i) => (
                <li key={`${r.code}-${i}`} className={i === 0 ? "reason-primary" : undefined}>
                  {r.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!linkToDetail && title.overview && (
          <p className="pick-overview">{title.overview}</p>
        )}

        <div className="pick-actions">{actions}</div>
      </div>
    </li>
  );
}
