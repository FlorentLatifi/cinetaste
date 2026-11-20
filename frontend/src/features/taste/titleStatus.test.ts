import { describe, expect, it } from "vitest";
import type { FeedbackAction } from "../../components/ActionToast";
import {
  canClearFromToggle,
  NO_STATUS,
  offersNotForMe,
  offersSkipRating,
  offersWatchlist,
  statusAfter,
  undoRequests,
  type ViewerStatus,
} from "./titleStatus";

/** Apply a sequence the way the API would (backend resolve_title_state). */
function replay(events: Array<FeedbackAction | "clear">): ViewerStatus {
  return events.reduce<ViewerStatus>((s, e) => statusAfter(s, e), NO_STATUS);
}

const LOVED: ViewerStatus = { state: "like", rating: "rate_4" };

describe("statusAfter mirrors the backend state rule", () => {
  it("keeps a rating when the title is saved afterwards", () => {
    expect(replay(["rate_4", "watchlist"])).toEqual(LOVED);
  });

  it("keeps a rating when the title is marked watched afterwards", () => {
    expect(replay(["rate_4", "watched"])).toEqual(LOVED);
  });

  it("keeps a rating when 'Haven't seen it' follows", () => {
    expect(replay(["rate_3", "haven't_seen"])).toEqual({ state: "like", rating: "rate_3" });
  });

  it("lets a rating replace a watchlist entry", () => {
    expect(replay(["watchlist", "rate_3"])).toEqual({ state: "like", rating: "rate_3" });
  });

  it("applies a rating given after a clear", () => {
    expect(replay(["rate_4", "clear", "rate_1"])).toEqual({ state: "dislike", rating: "rate_1" });
  });

  it("lets a later opinion replace an earlier one", () => {
    expect(replay(["rate_4", "rate_1"])).toEqual({ state: "dislike", rating: "rate_1" });
    expect(replay(["watchlist", "not_interested"])).toEqual({
      state: "not_interested",
      rating: null,
    });
  });

  it("lets the latest intent win among intents", () => {
    expect(replay(["watchlist", "watched"])).toEqual({ state: "watched", rating: null });
  });

  it("reports 'Haven't seen it' alone as no state", () => {
    expect(replay(["haven't_seen"])).toEqual(NO_STATUS);
  });
});

describe("the page never offers a way to erase a rating", () => {
  it("hides watchlist, not-for-me and skip-rating once rated", () => {
    expect(offersWatchlist(LOVED)).toBe(false);
    expect(offersNotForMe(LOVED)).toBe(false);
    expect(offersSkipRating(LOVED)).toBe(false);
    expect(canClearFromToggle(LOVED)).toBe(false);
  });

  it("offers them for an untouched or saved title", () => {
    const saved = { state: "watchlist", rating: null };
    for (const s of [NO_STATUS, saved]) {
      expect(offersWatchlist(s)).toBe(true);
      expect(offersNotForMe(s)).toBe(true);
      expect(offersSkipRating(s)).toBe(true);
      expect(canClearFromToggle(s)).toBe(true);
    }
  });

  it("treats shortcut likes without a numeric rating as rated", () => {
    expect(canClearFromToggle({ state: "like", rating: null })).toBe(false);
    expect(canClearFromToggle({ state: "rated", rating: null })).toBe(false);
  });

  it("lets 'Not for you' be toggled off, since no rating is underneath", () => {
    const rejected = { state: "not_interested", rating: null };
    expect(offersNotForMe(rejected)).toBe(true);
    expect(canClearFromToggle(rejected)).toBe(true);
    expect(offersWatchlist(rejected)).toBe(false);
  });
});

describe("undoRequests", () => {
  it("restores a previous rating with one request and no clear", () => {
    const after = statusAfter(LOVED, "rate_1");
    expect(undoRequests(LOVED, after)).toEqual(["rate_4"]);
  });

  it("steps back from a rating to the watchlist with clear, then watchlist", () => {
    const saved = { state: "watchlist", rating: null };
    expect(undoRequests(saved, statusAfter(saved, "rate_3"))).toEqual(["clear", "watchlist"]);
  });

  it("undoes the first action on an untouched title with a clear", () => {
    expect(undoRequests(NO_STATUS, statusAfter(NO_STATUS, "watchlist"))).toEqual(["clear"]);
  });

  it("never sends clear when the status being restored has a rating", () => {
    const all: Array<FeedbackAction> = [
      "watchlist",
      "watched",
      "not_interested",
      "rate_1",
      "mid",
      "rate_3",
      "rate_4",
    ];
    for (const event of all) {
      expect(undoRequests(LOVED, statusAfter(LOVED, event))).not.toContain("clear");
    }
  });
});
