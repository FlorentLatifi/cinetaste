import { describe, expect, it, vi } from "vitest";
import { parseTasteSnapshot } from "./snapshot";

/**
 * This is the only place the app parses a file the user chose, so it is the
 * one piece of frontend logic where malformed input is expected rather than
 * exceptional. It had no unit tests; the e2e suite only ever fed it a good
 * file.
 */

const VALID = {
  schema: "cinetaste.taste_snapshot.v1",
  exported_at: "2026-09-22T10:00:00+00:00",
  profile_version: 3,
  updated_at: "2026-09-20T09:00:00+00:00",
  has_vector: true,
  feature_count: 2,
  anchor_count: 1,
  likes: [{ key: "genre:drama", family: "genre", label: "Drama", weight: 1.2 }],
  dislikes: [{ key: "genre:horror", family: "genre", label: "Horror", weight: -0.8 }],
  anchors: [{ name: "Arrival", year: 2016 }],
  text: "Likes: Drama",
};

function expectRejected(input: unknown, matching: RegExp) {
  const result = parseTasteSnapshot(input);
  expect(result.ok).toBe(false);
  if (!result.ok) expect(result.error).toMatch(matching);
}

describe("a snapshot the export produced", () => {
  it("round-trips as an object", () => {
    const result = parseTasteSnapshot(VALID);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.likes).toHaveLength(1);
      expect(result.data.anchors[0]).toEqual({ name: "Arrival", year: 2016 });
    }
  });

  it("round-trips as the raw file contents", () => {
    const result = parseTasteSnapshot(JSON.stringify(VALID));
    expect(result.ok).toBe(true);
  });

  it("keeps working when the optional fields are absent", () => {
    const { updated_at, text, anchors, anchor_count, ...rest } = VALID;
    void updated_at;
    void text;
    void anchors;
    void anchor_count;

    const result = parseTasteSnapshot(rest);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.updated_at).toBeNull();
      expect(result.data.anchors).toEqual([]);
      expect(result.data.anchor_count).toBe(0);
    }
  });

  it("derives anchor_count from the anchors when it is missing", () => {
    const { anchor_count, ...rest } = VALID;
    void anchor_count;

    const result = parseTasteSnapshot(rest);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data.anchor_count).toBe(1);
  });
});

describe("input that is not a snapshot", () => {
  it("rejects a file that is not JSON", () => {
    expectRejected("not json at all {", /not valid JSON/i);
  });

  it("rejects JSON that is not an object", () => {
    expectRejected("[1, 2, 3]", /schema/i); // arrays are objects to typeof
    expectRejected('"a string"', /must be a JSON object/i);
    expectRejected("42", /must be a JSON object/i);
    expectRejected("null", /must be a JSON object/i);
  });

  it("rejects another product's export", () => {
    expectRejected({ ...VALID, schema: "letterboxd.v1" }, /Unsupported schema/i);
  });

  it("rejects a future schema version rather than guessing", () => {
    expectRejected({ ...VALID, schema: "cinetaste.taste_snapshot.v2" }, /Unsupported schema/i);
  });

  it("names the field that is wrong", () => {
    expectRejected({ ...VALID, exported_at: 1_700_000_000 }, /exported_at/i);
    expectRejected({ ...VALID, profile_version: "3" }, /profile_version/i);
  });
});

describe("feature rows", () => {
  it("rejects a weight that is not a number", () => {
    expectRejected(
      { ...VALID, likes: [{ key: "genre:drama", family: "genre", label: "Drama", weight: "1.2" }] },
      /likes/i,
    );
  });

  it("rejects NaN and Infinity, which survive JSON round-trips as strings", () => {
    const row = { key: "genre:drama", family: "genre", label: "Drama" };
    expectRejected({ ...VALID, likes: [{ ...row, weight: Number.NaN }] }, /likes/i);
    expectRejected({ ...VALID, likes: [{ ...row, weight: Number.POSITIVE_INFINITY }] }, /likes/i);
  });

  it("rejects a missing field in one row out of many", () => {
    expectRejected(
      {
        ...VALID,
        dislikes: [
          { key: "genre:horror", family: "genre", label: "Horror", weight: -0.8 },
          { key: "genre:war", family: "genre", weight: -0.5 },
        ],
      },
      /dislikes/i,
    );
  });

  it("rejects a list that is not a list", () => {
    expectRejected({ ...VALID, likes: {} }, /likes/i);
    expectRejected({ ...VALID, dislikes: null }, /dislikes/i);
  });

  it("accepts empty lists — a profile with no signals is still a snapshot", () => {
    const result = parseTasteSnapshot({ ...VALID, likes: [], dislikes: [] });
    expect(result.ok).toBe(true);
  });
});

describe("anchors", () => {
  it("accepts a missing year", () => {
    const result = parseTasteSnapshot({ ...VALID, anchors: [{ name: "Arrival" }] });
    expect(result.ok).toBe(true);
  });

  it("rejects a blank name", () => {
    expectRejected({ ...VALID, anchors: [{ name: "   " }] }, /anchors/i);
  });

  it("rejects a year that is not a number", () => {
    expectRejected({ ...VALID, anchors: [{ name: "Arrival", year: "2016" }] }, /anchors/i);
  });

  it("ignores anchors that are not a list rather than failing", () => {
    // The import endpoint does not use anchors, so a broken one is not worth
    // rejecting the whole file over.
    const result = parseTasteSnapshot({ ...VALID, anchors: "nope" });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data.anchors).toEqual([]);
  });
});

describe("prefersReducedMotion", () => {
  it("is false where there is no window (SSR, tests)", async () => {
    const { prefersReducedMotion } = await import("./snapshot");
    expect(prefersReducedMotion()).toBe(false);
  });

  it("follows the media query when the browser exposes one", async () => {
    const { prefersReducedMotion } = await import("./snapshot");
    vi.stubGlobal("window", {
      matchMedia: (q: string) => ({ matches: q.includes("reduce") }),
    });
    expect(prefersReducedMotion()).toBe(true);

    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
    expect(prefersReducedMotion()).toBe(false);

    // A very old browser with no matchMedia at all must not throw.
    vi.stubGlobal("window", {});
    expect(prefersReducedMotion()).toBe(false);

    vi.unstubAllGlobals();
  });
});
