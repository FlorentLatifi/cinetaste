import type { Title } from "../api/titles";

/** Resolve a displayable poster URL from API fields. */
export function posterSrc(
  title: Pick<Title, "poster_url" | "poster_path">,
): string | null {
  if (title.poster_url) return title.poster_url;
  if (title.poster_path?.startsWith("http")) return title.poster_path;
  if (title.poster_path?.startsWith("/")) {
    return `https://image.tmdb.org/t/p/w500${title.poster_path}`;
  }
  return null;
}

/** Prefer a larger TMDb size for hero / immersive posters. */
export function heroPosterUrl(
  title: Pick<Title, "poster_url" | "poster_path">,
): string | null {
  const raw = title.poster_path || title.poster_url || "";
  if (raw.startsWith("http") && raw.includes("/w500")) {
    return raw.replace("/w500", "/w780");
  }
  if (raw.startsWith("/") && !raw.startsWith("http")) {
    return `https://image.tmdb.org/t/p/w780${raw}`;
  }
  return posterSrc(title);
}

export function yearOf(title: Pick<Title, "release_date">): string | null {
  return title.release_date ? title.release_date.slice(0, 4) : null;
}
