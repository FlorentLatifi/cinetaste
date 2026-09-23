import { describe, expect, it } from "vitest";
import {
  ONBOARDING_RATINGS,
  POST_WATCH_RATINGS,
  RATING_DONE_LABELS,
  RATING_SCALE,
  ratingLabel,
} from "./ratingScale";

/**
 * This module exists because four surfaces used four different words for the
 * same button and the confirmation toast looked like it had recorded something
 * else. These tests guard the properties that made it worth centralising —
 * not the wording, which is allowed to change.
 */

describe("the scale is internally consistent", () => {
  it("has no duplicate events", () => {
    const events = RATING_SCALE.map((o) => o.event);
    expect(new Set(events).size).toBe(events.length);
  });

  it("has no duplicate number keys", () => {
    // Two options on the same key means one of them is unreachable on For You.
    const keys = RATING_SCALE.map((o) => o.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("has no duplicate letter shortcuts", () => {
    const shortcuts = RATING_SCALE.map((o) => o.shortcut.toLowerCase());
    expect(new Set(shortcuts).size).toBe(shortcuts.length);
  });

  it("numbers the keys 1..n in display order", () => {
    expect(RATING_SCALE.map((o) => o.key)).toEqual(
      RATING_SCALE.map((_, i) => String(i + 1)),
    );
  });

  it("gives every option the text each surface needs", () => {
    for (const option of RATING_SCALE) {
      expect(option.label.trim(), option.event).not.toBe("");
      expect(option.done.trim(), option.event).not.toBe("");
      expect(option.hint.trim(), option.event).not.toBe("");
      expect(option.emoji.trim(), option.event).not.toBe("");
      expect(option.className.trim(), option.event).not.toBe("");
    }
  });
});

describe("the derived lists stay in step with the scale", () => {
  it("covers every event in RATING_DONE_LABELS", () => {
    expect(Object.keys(RATING_DONE_LABELS).sort()).toEqual(
      RATING_SCALE.map((o) => o.event).sort(),
    );
    for (const option of RATING_SCALE) {
      expect(RATING_DONE_LABELS[option.event]).toBe(option.done);
    }
  });

  it("builds ONBOARDING_RATINGS from real scale entries", () => {
    // `.find(...)!` in the source would put `undefined` in the array if an
    // event were ever renamed, and the UI would render a blank button.
    for (const option of ONBOARDING_RATINGS) {
      expect(option).toBeDefined();
      expect(RATING_SCALE).toContain(option);
    }
  });

  it("omits \"haven't seen\" from the rating lists", () => {
    // It is not an opinion, and offering it after watching makes no sense.
    expect(ONBOARDING_RATINGS.map((o) => o.event)).not.toContain("haven't_seen");
  });

  it("orders onboarding worst to best, the reverse of For You", () => {
    const feedOrder = RATING_SCALE.map((o) => o.event).filter(
      (e) => e !== "haven't_seen",
    );
    expect(ONBOARDING_RATINGS.map((o) => o.event)).toEqual([...feedOrder].reverse());
  });

  it("reuses the onboarding list after watching", () => {
    expect(POST_WATCH_RATINGS).toBe(ONBOARDING_RATINGS);
  });
});

describe("ratingLabel", () => {
  it("returns the same words the button shows", () => {
    for (const option of RATING_SCALE) {
      expect(ratingLabel(option.event)).toBe(option.label);
    }
  });

  it("returns undefined for actions that are not ratings", () => {
    // watchlist / not_interested / clear are FeedbackActions too; the caller
    // falls back to its own wording rather than rendering "undefined".
    expect(ratingLabel("watchlist")).toBeUndefined();
    expect(ratingLabel("not_interested")).toBeUndefined();
  });
});
