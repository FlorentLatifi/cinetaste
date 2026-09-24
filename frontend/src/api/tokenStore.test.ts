import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearLegacyTokenStorage, getAccessToken, setAccessToken } from "./tokenStore";

/**
 * Small module, load-bearing contract: the access token lives in memory and
 * nowhere else. An XSS bug can read localStorage; it cannot read a closure.
 */

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("the access token", () => {
  it("starts empty", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("round-trips in memory", () => {
    setAccessToken("header.payload.signature");
    expect(getAccessToken()).toBe("header.payload.signature");
  });

  it("can be cleared", () => {
    setAccessToken("x");
    setAccessToken(null);
    expect(getAccessToken()).toBeNull();
  });

  it("never reaches web storage", () => {
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { setItem, getItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal("sessionStorage", { setItem, getItem: vi.fn(), removeItem: vi.fn() });

    setAccessToken("header.payload.signature");

    expect(setItem).not.toHaveBeenCalled();
  });
});

describe("clearing storage written by older clients", () => {
  let removeItem: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    removeItem = vi.fn();
    vi.stubGlobal("localStorage", { removeItem });
  });

  it("removes both legacy keys", () => {
    clearLegacyTokenStorage();
    expect(removeItem.mock.calls.map(([k]) => k)).toEqual(["ct_access", "ct_refresh"]);
  });

  it("survives storage being blocked", () => {
    // Safari private mode throws on access rather than returning null, and this
    // runs on every page load — throwing here would blank the whole app.
    vi.stubGlobal("localStorage", {
      removeItem: () => {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });

    expect(() => clearLegacyTokenStorage()).not.toThrow();
  });

  it("survives there being no storage object at all", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(() => clearLegacyTokenStorage()).not.toThrow();
  });
});
