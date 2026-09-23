import { describe, expect, it } from "vitest";
import { heroPosterUrl, posterSrc, posterSrcSet, tmdbPosterPath, yearOf } from "./poster";

type Poster = { poster_path: string | null; poster_url: string | null };

/**
 * Posters come from TMDb in three shapes depending on which endpoint answered:
 * a bare `/abc.jpg` path, a full w500 URL the API pre-built, or nothing at all.
 * Getting this wrong shows a broken image, which the e2e suite cannot tell
 * apart from a slow one.
 */

const PATH: Poster = { poster_path: "/abc123.jpg", poster_url: null };
const URL_W500: Poster = {
  poster_path: null,
  poster_url: "https://image.tmdb.org/t/p/w500/abc123.jpg",
};
const NOTHING: Poster = { poster_path: null, poster_url: null };
const FOREIGN: Poster = { poster_path: null, poster_url: "https://example.com/art.png" };

describe("tmdbPosterPath", () => {
  it("passes a bare path through", () => {
    expect(tmdbPosterPath(PATH)).toBe("/abc123.jpg");
  });

  it("extracts the path out of a sized TMDb URL", () => {
    expect(tmdbPosterPath(URL_W500)).toBe("/abc123.jpg");
  });

  it("returns null for a URL that is not TMDb", () => {
    expect(tmdbPosterPath(FOREIGN)).toBeNull();
  });

  it("returns null when there is nothing to work with", () => {
    expect(tmdbPosterPath(NOTHING)).toBeNull();
    expect(tmdbPosterPath({ poster_path: "", poster_url: "" })).toBeNull();
  });
});

describe("posterSrc", () => {
  it("prefers the URL the API already built", () => {
    expect(posterSrc({ ...URL_W500, poster_path: "/other.jpg" })).toBe(
      "https://image.tmdb.org/t/p/w500/abc123.jpg",
    );
  });

  it("builds a w500 URL from a bare path", () => {
    expect(posterSrc(PATH)).toBe("https://image.tmdb.org/t/p/w500/abc123.jpg");
  });

  it("accepts a full URL stored in poster_path", () => {
    expect(posterSrc({ poster_path: "https://cdn.test/x.jpg", poster_url: null })).toBe(
      "https://cdn.test/x.jpg",
    );
  });

  it("returns null rather than a broken src", () => {
    expect(posterSrc(NOTHING)).toBeNull();
  });
});

describe("heroPosterUrl", () => {
  it("upgrades a TMDb poster to w780", () => {
    expect(heroPosterUrl(PATH)).toBe("https://image.tmdb.org/t/p/w780/abc123.jpg");
    expect(heroPosterUrl(URL_W500)).toBe("https://image.tmdb.org/t/p/w780/abc123.jpg");
  });

  it("falls back to the ordinary source for a non-TMDb image", () => {
    expect(heroPosterUrl(FOREIGN)).toBe("https://example.com/art.png");
  });

  it("returns null when there is no image at all", () => {
    expect(heroPosterUrl(NOTHING)).toBeNull();
  });
});

describe("posterSrcSet", () => {
  it("offers every width with its descriptor", () => {
    expect(posterSrcSet(PATH)).toBe(
      [
        "https://image.tmdb.org/t/p/w185/abc123.jpg 185w",
        "https://image.tmdb.org/t/p/w342/abc123.jpg 342w",
        "https://image.tmdb.org/t/p/w500/abc123.jpg 500w",
        "https://image.tmdb.org/t/p/w780/abc123.jpg 780w",
      ].join(", "),
    );
  });

  it("honours a custom width list", () => {
    expect(posterSrcSet(PATH, [92])).toBe("https://image.tmdb.org/t/p/w92/abc123.jpg 92w");
  });

  it("returns null for images TMDb cannot resize", () => {
    // A srcset of one size is worse than none: the browser would download the
    // wrong one believing it had a choice.
    expect(posterSrcSet(FOREIGN)).toBeNull();
    expect(posterSrcSet(NOTHING)).toBeNull();
  });
});

describe("yearOf", () => {
  it("takes the year off an ISO date", () => {
    expect(yearOf({ release_date: "2016-11-11" })).toBe("2016");
  });

  it("returns null for an unreleased title", () => {
    expect(yearOf({ release_date: null })).toBeNull();
    expect(yearOf({ release_date: "" })).toBeNull();
  });
});

describe("mirrors and proxies that are not image.tmdb.org", () => {
  it("still upgrades a w500 in the path when the URL shape is unfamiliar", () => {
    // A CDN in front of TMDb keeps the size segment but not /t/p/, so the
    // regex misses and the string replace is the only thing left.
    const mirrored: Poster = { poster_path: null, poster_url: "https://cdn.test/w500/abc.jpg" };
    expect(heroPosterUrl(mirrored)).toBe("https://cdn.test/w780/abc.jpg");
  });
});
