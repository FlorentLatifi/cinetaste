import { describe, expect, it } from "vitest";
import { evaluatePassword } from "./passwordStrength";

/**
 * Guidance only — the server enforces the real minimum. What matters here is
 * that the meter never tells someone a password is fine when the API will
 * reject it, and never leaves the bar at a score with no label.
 */

describe("scoring", () => {
  it("scores an empty password 0 with no label", () => {
    const result = evaluatePassword("");
    expect(result.score).toBe(0);
    expect(result.label).toBe("");
  });

  it("climbs one step per rule met", () => {
    expect(evaluatePassword("aaaa").score).toBe(1); // letter only
    expect(evaluatePassword("aaaa1").score).toBe(2); // + number
    expect(evaluatePassword("aaaa1!").score).toBe(3); // + symbol
    expect(evaluatePassword("aaaaaaa1!").score).toBe(4); // + length
  });

  it("gives every non-empty score a label", () => {
    for (const password of ["a", "a1", "a1!", "aaaaaaa1!"]) {
      expect(evaluatePassword(password).label, password).not.toBe("");
    }
  });

  it("reaches the top score only when all four rules pass", () => {
    // Long but nothing else: still not "Strong".
    expect(evaluatePassword("aaaaaaaaaaaaaaaaaaaa").score).toBeLessThan(4);
    expect(evaluatePassword("Password1!").score).toBe(4);
  });
});

describe("the rules themselves", () => {
  const met = (password: string) =>
    Object.fromEntries(evaluatePassword(password).checks.map((c) => [c.id, c.met]));

  it("requires eight characters, counting the eighth", () => {
    expect(met("abcdefg").length).toBe(false);
    expect(met("abcdefgh").length).toBe(true);
  });

  it("accepts either case as a letter", () => {
    expect(met("Z").letter).toBe(true);
    expect(met("z").letter).toBe(true);
    expect(met("1234").letter).toBe(false);
  });

  it("treats anything outside a-z0-9 as a symbol", () => {
    expect(met("a ").symbol).toBe(true); // space
    expect(met("a_").symbol).toBe(true);
    expect(met("café").symbol).toBe(true); // accented letters count too
    expect(met("abc123").symbol).toBe(false);
  });

  it("reports the same four checks whatever the input", () => {
    for (const password of ["", "a", "Password1!"]) {
      expect(evaluatePassword(password).checks.map((c) => c.id)).toEqual([
        "length",
        "letter",
        "number",
        "symbol",
      ]);
    }
  });

  it("gives every check text to render", () => {
    for (const check of evaluatePassword("x").checks) {
      expect(check.label.trim()).not.toBe("");
    }
  });
});

describe("inputs that break naive implementations", () => {
  it("counts a password that is only symbols", () => {
    const result = evaluatePassword("!!!!!!!!");
    expect(result.score).toBe(2); // length + symbol
  });

  it("does not crash on the 128-character maximum the API allows", () => {
    const result = evaluatePassword("aA1!".repeat(32));
    expect(result.score).toBe(4);
  });

  it("handles whitespace-only input without claiming it is strong", () => {
    expect(evaluatePassword("        ").score).toBeLessThan(3);
  });
});
