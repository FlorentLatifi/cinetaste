import { afterEach, describe, expect, it, vi } from "vitest";
import { broadcastSession, onSessionEvent } from "./sessionChannel";

/**
 * The contract that matters here is negative: a timestamp crosses between tabs,
 * never a token. An access token in localStorage would undo the reason it lives
 * in memory.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubStorage() {
  const store = new Map<string, string>();
  const setItem = vi.fn((k: string, v: string) => void store.set(k, v));
  vi.stubGlobal("localStorage", { setItem, getItem: (k: string) => store.get(k) ?? null });
  return { store, setItem };
}

function stubWindow() {
  const listeners: ((e: StorageEvent) => void)[] = [];
  vi.stubGlobal("window", {
    addEventListener: (_: string, fn: (e: StorageEvent) => void) => listeners.push(fn),
    removeEventListener: (_: string, fn: (e: StorageEvent) => void) => {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    },
  });
  return {
    listeners,
    fire: (key: string | null, newValue: string | null) =>
      listeners.forEach((fn) => fn({ key, newValue } as StorageEvent)),
  };
}

describe("broadcasting", () => {
  it("writes the event under one key", () => {
    const { setItem } = stubStorage();
    broadcastSession("signed-out");

    expect(setItem).toHaveBeenCalledTimes(1);
    const [key, value] = setItem.mock.calls[0];
    expect(key).toBe("ct_session_event");
    expect(value).toMatch(/^signed-out:\d+$/);
  });

  it("never writes anything token-shaped", () => {
    const { setItem } = stubStorage();
    broadcastSession("signed-in");

    const value = String(setItem.mock.calls[0][1]);
    expect(value).not.toMatch(/\./); // a JWT has dots
    expect(value.split(":")[0]).toBe("signed-in");
  });

  it("makes each write distinct", () => {
    // `storage` does not fire when the value is unchanged, so two sign-outs in
    // a row would be silent without the timestamp.
    const { setItem } = stubStorage();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    broadcastSession("signed-out");
    vi.setSystemTime(new Date("2026-01-01T00:00:05Z"));
    broadcastSession("signed-out");
    vi.useRealTimers();

    expect(setItem.mock.calls[0][1]).not.toBe(setItem.mock.calls[1][1]);
  });

  it("survives storage being blocked", () => {
    // Safari private mode throws rather than returning null. Tabs just stay
    // independent; nothing else should break.
    vi.stubGlobal("localStorage", {
      setItem: () => {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });
    expect(() => broadcastSession("signed-out")).not.toThrow();
  });
});

describe("listening", () => {
  it("reports events from other tabs", () => {
    const { fire } = stubWindow();
    const heard: string[] = [];
    onSessionEvent((e) => heard.push(e));

    fire("ct_session_event", "signed-out:123");
    fire("ct_session_event", "signed-in:456");

    expect(heard).toEqual(["signed-out", "signed-in"]);
  });

  it("ignores other keys", () => {
    const { fire } = stubWindow();
    const heard: string[] = [];
    onSessionEvent((e) => heard.push(e));

    fire("ct_color_scheme", "dark");
    fire("ct_contrast", "high");

    expect(heard).toEqual([]);
  });

  it("ignores a cleared value and an unknown event", () => {
    const { fire } = stubWindow();
    const heard: string[] = [];
    onSessionEvent((e) => heard.push(e));

    fire("ct_session_event", null);
    fire("ct_session_event", "something-else:1");

    expect(heard).toEqual([]);
  });

  it("unsubscribes", () => {
    const { fire, listeners } = stubWindow();
    const heard: string[] = [];
    const off = onSessionEvent((e) => heard.push(e));

    off();
    fire("ct_session_event", "signed-out:1");

    expect(listeners).toHaveLength(0);
    expect(heard).toEqual([]);
  });

  it("is a no-op where there is no window", () => {
    vi.stubGlobal("window", undefined);
    expect(() => onSessionEvent(() => undefined)()).not.toThrow();
  });
});
