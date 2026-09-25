import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearGuestDraft,
  hasGuestDraft,
  loadGuestDraft,
  saveGuestDraft,
} from "./guestDraft";

/**
 * The draft is how a guest's answers survive sign-up, so the failure modes
 * that matter are: losing it too early, keeping it forever, and trusting
 * whatever happens to be in localStorage under our key.
 */

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.data.set(k, v);
  }
  removeItem(k: string) {
    this.data.delete(k);
  }
}

let store: MemoryStorage;

beforeEach(() => {
  store = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: store });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const DAY = 24 * 60 * 60 * 1000;

describe("guest draft", () => {
  it("round-trips answers and saved picks", () => {
    saveGuestDraft(
      {
        reactions: [
          { title_id: "a", action: "rate_4" },
          { title_id: "b", action: "haven't_seen" },
        ],
        saved: ["x"],
      },
      1_000,
    );
    const draft = loadGuestDraft(2_000);
    expect(draft.reactions).toEqual([
      { title_id: "a", action: "rate_4" },
      { title_id: "b", action: "haven't_seen" },
    ]);
    expect(draft.saved).toEqual(["x"]);
    expect(hasGuestDraft(draft)).toBe(true);
  });

  it("keeps one answer per title, the latest", () => {
    saveGuestDraft({
      reactions: [
        { title_id: "a", action: "rate_4" },
        { title_id: "b", action: "rate_2" },
        { title_id: "a", action: "rate_1" },
      ],
      saved: ["x", "x"],
    });
    const draft = loadGuestDraft();
    expect(draft.reactions).toEqual([
      { title_id: "b", action: "rate_2" },
      { title_id: "a", action: "rate_1" },
    ]);
    expect(draft.saved).toEqual(["x"]);
  });

  it("expires after a week", () => {
    saveGuestDraft({ reactions: [{ title_id: "a", action: "rate_4" }], saved: [] }, 0);
    expect(loadGuestDraft(6 * DAY).reactions).toHaveLength(1);
    expect(loadGuestDraft(8 * DAY).reactions).toHaveLength(0);
    // And the stale copy is removed, not just ignored.
    expect(store.data.size).toBe(0);
  });

  it("drops entries that are not answers it could have written", () => {
    store.setItem(
      "cinetaste.guestDraft.v1",
      JSON.stringify({
        savedAt: Date.now(),
        reactions: [
          { title_id: "a", action: "rate_4" },
          { title_id: "b", action: "delete_account" },
          { title_id: 7, action: "rate_4" },
          "nonsense",
        ],
        saved: ["x", 3, null],
      }),
    );
    const draft = loadGuestDraft();
    expect(draft.reactions).toEqual([{ title_id: "a", action: "rate_4" }]);
    expect(draft.saved).toEqual(["x"]);
  });

  it("treats corrupt JSON as no draft", () => {
    store.setItem("cinetaste.guestDraft.v1", "{not json");
    expect(hasGuestDraft(loadGuestDraft())).toBe(false);
  });

  it("works as a no-op when storage is blocked", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new Error("SecurityError");
      },
    });
    expect(() => saveGuestDraft({ reactions: [], saved: ["x"] })).not.toThrow();
    expect(hasGuestDraft(loadGuestDraft())).toBe(false);
    expect(() => clearGuestDraft()).not.toThrow();
  });

  it("clears", () => {
    saveGuestDraft({ reactions: [{ title_id: "a", action: "rate_4" }], saved: [] });
    clearGuestDraft();
    expect(hasGuestDraft(loadGuestDraft())).toBe(false);
  });
});
