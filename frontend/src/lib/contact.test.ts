import { describe, expect, it } from "vitest";
import { contactEmailFrom, ISSUES_URL } from "./contact";

describe("contactEmailFrom", () => {
  it("is null when the setting is missing or blank", () => {
    expect(contactEmailFrom(undefined)).toBeNull();
    expect(contactEmailFrom("")).toBeNull();
    expect(contactEmailFrom("   ")).toBeNull();
  });

  it("returns the address without surrounding whitespace", () => {
    expect(contactEmailFrom("  hello@cinetaste.app \n")).toBe("hello@cinetaste.app");
  });

  it("ignores values that are not an address", () => {
    expect(contactEmailFrom("TODO")).toBeNull();
    expect(contactEmailFrom("hello@localhost")).toBeNull();
    expect(contactEmailFrom("two words@example.com")).toBeNull();
  });
});

describe("ISSUES_URL", () => {
  it("points at the public issue tracker", () => {
    expect(ISSUES_URL).toMatch(/^https:\/\/github\.com\/[^/]+\/cinetaste\/issues$/);
  });
});
