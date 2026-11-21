import { describe, expect, it } from "vitest";
import { returnPath, returnState } from "./returnTo";

describe("returnPath", () => {
  it("returns an in-app path with its query", () => {
    expect(returnPath({ from: "/titles/abc" })).toBe("/titles/abc");
    expect(returnPath({ from: "/search?q=heat" })).toBe("/search?q=heat");
  });

  it("ignores missing or malformed state", () => {
    expect(returnPath(null)).toBeNull();
    expect(returnPath(undefined)).toBeNull();
    expect(returnPath({})).toBeNull();
    expect(returnPath({ from: 42 })).toBeNull();
    expect(returnPath({ from: "" })).toBeNull();
  });

  it("refuses anything that would leave the app", () => {
    expect(returnPath({ from: "https://evil.example/" })).toBeNull();
    expect(returnPath({ from: "//evil.example/path" })).toBeNull();
    expect(returnPath({ from: "/\\evil.example" })).toBeNull();
    expect(returnPath({ from: "javascript:alert(1)" })).toBeNull();
    expect(returnPath({ from: "/titles/x\u0000" })).toBeNull();
  });

  it("does not send a signed-in user back to a sign-in page", () => {
    expect(returnPath({ from: "/login" })).toBeNull();
    expect(returnPath({ from: "/register?x=1" })).toBeNull();
    expect(returnPath({ from: "/reset-password?token=t" })).toBeNull();
  });

  it("round-trips through returnState", () => {
    expect(returnPath(returnState("/titles/abc", "?tab=cast", "#where"))).toBe(
      "/titles/abc?tab=cast#where",
    );
  });
});
