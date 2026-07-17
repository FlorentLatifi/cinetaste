import type { Title } from "../api/titles";

type PosterFields = Pick<Title, "poster_url" | "poster_path">;

/** TMDb path starting with `/…` when we can derive one. */
export function tmdbPosterPath(title: PosterFields): string | null {
  if (title.poster_path?.startsWith("/")) return title.poster_path;
  const url = title.poster_url || title.poster_path || "";
  if (!url.startsWith("http")) return null;
  const match = url.match(/\/t\/p\/w\d+(\/.*)$/);
  return match?.[1] ?? null;
}

/** Resolve a displayable poster URL from API fields (w500 default). */
export function posterSrc(title: PosterFields): string | null {
  if (title.poster_url) return title.poster_url;
  if (title.poster_path?.startsWith("http")) return title.poster_path;
  if (title.poster_path?.startsWith("/")) {
    return `https://image.tmdb.org/t/p/w500${title.poster_path}`;
  }
  return null;
}

/** Prefer a larger TMDb size for hero / immersive posters. */
export function heroPosterUrl(title: PosterFields): string | null {
  const path = tmdbPosterPath(title);
  if (path) return `https://image.tmdb.org/t/p/w780${path}`;
  const raw = title.poster_path || title.poster_url || "";
  if (raw.startsWith("http") && raw.includes("/w500")) {
    return raw.replace("/w500", "/w780");
  }
  return posterSrc(title);
}

/**
 * Responsive TMDb srcset (w185 → w780). Returns null when we cannot build sizes.
 */
export function posterSrcSet(
  title: PosterFields,
  widths: number[] = [185, 342, 500, 780],
): string | null {
  const path = tmdbPosterPath(title);
  if (!path) return null;
  return widths
    .map((w) => `https://image.tmdb.org/t/p/w${w}${path} ${w}w`)
    .join(", ");
}

export function yearOf(title: Pick<Title, "release_date">): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}
