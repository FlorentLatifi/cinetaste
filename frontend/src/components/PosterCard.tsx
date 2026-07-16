import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Title } from "../api/titles";

export function posterSrc(title: Pick<Title, "poster_url" | "poster_path">): string | null {
  if (title.poster_url) return title.poster_url;
  if (title.poster_path?.startsWith("http")) return title.poster_path;
  if (title.poster_path?.startsWith("/")) {
    return `https://image.tmdb.org/t/p/w500${title.poster_path}`;
  }
  return null;
}

export function yearOf(title: Pick<Title, "release_date">): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}

type Props = {
  title: Title;
  /** Extra content under the poster (reasons, actions) */
  children?: ReactNode;
  /** Badge over the poster (e.g. Liked, Hidden gem) */
  badge?: ReactNode;
  /** Compact catalog tile (search / watchlist / history) */
  compact?: boolean;
  className?: string;
  /** Heading level for the title */
  headingLevel?: "h2" | "h3";
};

/**
 * Poster-forward title card: cover image is the primary visual.
 */
export function PosterCard({
  title,
  children,
  badge,
  compact = false,
  className = "",
  headingLevel = "h3",
}: Props) {
  const name = title.name;
  const src = posterSrc(title);
  const year = yearOf(title);
  const Heading = headingLevel;

  return (
    <article className={`poster-card ${compact ? "compact" : ""} ${className}`.trim()}>
      <Link
        to={`/titles/${title.id}`}
        className="poster-card-cover"
        aria-label={`${name}${year ? `, ${year}` : ""}`}
      >
        <div className="poster-card-frame">
          {src ? (
            <img src={src} alt="" loading="lazy" decoding="async" />
          ) : (
            <div className="poster-fallback" aria-hidden="true">
              <span className="poster-fallback-letter">{name.slice(0, 1)}</span>
              <span className="poster-fallback-name">{name}</span>
            </div>
          )}
          <div className="poster-card-shade" aria-hidden="true" />
          {badge && <div className="poster-card-badge-slot">{badge}</div>}
          <div className="poster-card-caption">
            <Heading className="poster-card-title">{name}</Heading>
            <p className="poster-card-meta">
              <span className="poster-card-type">{title.media_type}</span>
              {year && <span> · {year}</span>}
              {title.vote_average > 0 && (
                <span> · ★ {title.vote_average.toFixed(1)}</span>
              )}
            </p>
          </div>
        </div>
      </Link>
      {children && <div className="poster-card-body">{children}</div>}
    </article>
  );
}
