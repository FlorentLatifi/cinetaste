import { afterEach, describe, expect, it, vi } from "vitest";
import { hasSessionHint, setSessionHint } from "./sessionHint";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("session hint", () => {
  it("is absent for a first-time visitor and follows sign-in and sign-out", () => {
    const store = memoryStorage();
    vi.stubGlobal("window", { localStorage: store });

    expect(hasSessionHint()).toBe(false);
    setSessionHint(true);
    expect(hasSessionHint()).toBe(true);
    setSessionHint(false);
    expect(hasSessionHint()).toBe(false);
    expect(store.data.size).toBe(0);
  });

  it("only trusts the exact value it writes", () => {
    const store = memoryStorage();
    store.setItem("ct_signed_in", "true");
    vi.stubGlobal("window", { localStorage: store });
    expect(hasSessionHint()).toBe(false);
  });

  it("degrades to 'first-time visitor' when storage is blocked", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new Error("SecurityError");
      },
    });
    expect(hasSessionHint()).toBe(false);
    expect(() => setSessionHint(true)).not.toThrow();
  });
});
