type Props = {
  /** Number of poster tiles to show */
  count?: number;
  /** Accessible loading message */
  label?: string;
  className?: string;
};

/**
 * Shimmer poster grid used while catalog lists load (Search / History / Watchlist).
 */
export function CatalogSkeleton({
  count = 8,
  label = "Loading titles",
  className = "",
}: Props) {
  return (
    <div
      className={`catalog-skeleton ${className}`.trim()}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <ul className="poster-grid catalog" aria-hidden="true">
        {Array.from({ length: count }, (_, i) => (
          <li key={i}>
            <div className="catalog-skeleton-card">
              <div className="catalog-skeleton-poster shimmer" />
              <div className="catalog-skeleton-line shimmer" />
              <div className="catalog-skeleton-line short shimmer" />
            </div>
          </li>
        ))}
      </ul>
      <p className="sr-only">{label}</p>
    </div>
  );
}

/** Title detail loading placeholder — poster + text blocks. */
export function DetailSkeleton({ label = "Loading title" }: { label?: string }) {
  return (
    <div
      className="detail-skeleton"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="detail-skeleton-grid" aria-hidden="true">
        <div className="detail-skeleton-poster shimmer" />
        <div className="detail-skeleton-body">
          <div className="detail-skeleton-line title shimmer" />
          <div className="detail-skeleton-line short shimmer" />
          <div className="detail-skeleton-line shimmer" />
          <div className="detail-skeleton-line shimmer" />
          <div className="detail-skeleton-line mid shimmer" />
          <div className="detail-skeleton-actions">
            <span className="shimmer" />
            <span className="shimmer" />
            <span className="shimmer" />
          </div>
        </div>
      </div>
      <p className="sr-only">{label}</p>
    </div>
  );
}
