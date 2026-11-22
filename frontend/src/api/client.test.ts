import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError, setSessionExpiredHandler } from "./client";
import { getAccessToken, setAccessToken } from "./tokenStore";

/**
 * The session plumbing every page relies on: which token goes out, and what a
 * failed refresh means. Both were wrong in ways that signed people out while
 * they were rating titles.
 */

type Call = { url: string; auth: string | null };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let calls: Call[];
let expired: ReturnType<typeof vi.fn<() => void>>;

function stubFetch(handler: (url: string, auth: string | null) => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const auth = new Headers(init.headers).get("Authorization");
      calls.push({ url, auth });
      return handler(url, auth);
    }),
  );
}

beforeEach(() => {
  calls = [];
  expired = vi.fn<() => void>();
  setSessionExpiredHandler(expired);
});

afterEach(() => {
  setAccessToken(null);
  setSessionExpiredHandler(null);
  vi.unstubAllGlobals();
});

describe("which access token is sent", () => {
  it("prefers the refreshed token over the copy a page was given at sign-in", async () => {
    setAccessToken("fresh");
    stubFetch(() => jsonResponse(200, { ok: true }));

    await apiFetch("/me", {}, "stale-from-sign-in");

    expect(calls).toHaveLength(1);
    expect(calls[0].auth).toBe("Bearer fresh");
  });

  it("falls back to the caller's token before the store has one", async () => {
    stubFetch(() => jsonResponse(200, { ok: true }));

    await apiFetch("/me", {}, "only-token");

    expect(calls[0].auth).toBe("Bearer only-token");
  });

  it("refreshes once, then keeps using the new token without another refresh", async () => {
    setAccessToken("expired");
    stubFetch((url, auth) => {
      if (url.endsWith("/auth/refresh")) {
        return jsonResponse(200, { access_token: "renewed", token_type: "bearer", user: {} });
      }
      return auth === "Bearer renewed" ? jsonResponse(200, {}) : jsonResponse(401, {});
    });

    await apiFetch("/titles/a", {}, "expired");
    await apiFetch("/titles/b", {}, "expired");
    await apiFetch("/titles/c", {}, "expired");

    const refreshes = calls.filter((c) => c.url.endsWith("/auth/refresh"));
    expect(refreshes).toHaveLength(1);
    expect(getAccessToken()).toBe("renewed");
  });
});

describe("when the refresh itself fails", () => {
  it("keeps the session when the refresh is rate limited", async () => {
    setAccessToken("expired");
    stubFetch((url) =>
      url.endsWith("/auth/refresh")
        ? jsonResponse(429, { code: "rate_limited", message: "Too many requests." })
        : jsonResponse(401, {}),
    );

    const err = await apiFetch("/titles/a/interactions", { method: "POST", body: "{}" }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect(expired).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe("expired");
  });

  it("keeps the session when the server can't be reached", async () => {
    setAccessToken("expired");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/auth/refresh")) throw new TypeError("Failed to fetch");
        return jsonResponse(401, {});
      }),
    );

    const err = await apiFetch("/me").catch((e: unknown) => e);

    expect((err as ApiError).isTransport).toBe(true);
    expect(expired).not.toHaveBeenCalled();
  });

  it("ends the session when the server refuses the refresh token", async () => {
    setAccessToken("expired");
    stubFetch((url) =>
      url.endsWith("/auth/refresh")
        ? jsonResponse(401, { code: "invalid_refresh", message: "Invalid refresh token" })
        : jsonResponse(401, {}),
    );

    await apiFetch("/me").catch(() => undefined);

    expect(expired).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });
});
