import type { Page, Route } from "@playwright/test";

/** Stable mock user for authenticated axe / smoke tests (no real API). */
export const mockUserComplete = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "a11y@cinetaste.test",
  display_name: "A11y Tester",
  onboarding_completed_at: "2026-01-15T12:00:00.000Z",
  created_at: "2026-01-01T00:00:00.000Z",
};

export const mockUserNeedsOnboarding = {
  ...mockUserComplete,
  onboarding_completed_at: null,
};

export const mockTitle = {
  id: "22222222-2222-4222-8222-222222222222",
  media_type: "movie",
  name: "Mock Classic",
  overview: "A sample title used for accessibility tests.",
  release_date: "2010-07-16",
  runtime: 120,
  popularity: 40,
  vote_average: 7.8,
  poster_path: null,
  backdrop_path: null,
  original_language: "en",
  genres: [{ id: "33333333-3333-4333-8333-333333333333", name: "Drama" }],
  poster_url: null,
};

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  };
}

/**
 * Intercept SPA API calls so authenticated routes render without a backend.
 * Matches Vite preview / production base path `/api/v1`.
 */
export async function installApiMock(
  page: Page,
  opts: { onboardingComplete?: boolean } = {},
): Promise<void> {
  const onboardingComplete = opts.onboardingComplete !== false;
  const user = onboardingComplete ? mockUserComplete : mockUserNeedsOnboarding;

  await page.route("**/api/v1/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    // pathname may be /api/v1/... 
    let path = url.pathname;
    const idx = path.indexOf("/api/v1");
    if (idx >= 0) path = path.slice(idx + "/api/v1".length) || "/";
    if (!path.startsWith("/")) path = `/${path}`;

    const method = req.method();

    if (method === "POST" && path === "/auth/refresh") {
      await route.fulfill(
        json({
          access_token: "mock-access-token",
          token_type: "bearer",
          user,
        }),
      );
      return;
    }

    if (method === "POST" && path === "/auth/logout") {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (method === "GET" && path === "/me") {
      await route.fulfill(json(user));
      return;
    }

    if (method === "GET" && path.startsWith("/recommendations/for-you")) {
      await route.fulfill(
        json({
          items: [
            {
              title: mockTitle,
              score: 0.91,
              reasons: [
                {
                  code: "shared_genre",
                  message: "Fits the drama side of your taste",
                  evidence: { genres: ["Drama"] },
                },
                {
                  code: "hidden_gem",
                  message:
                    "Highly rated (★7.8) but not a chart-topper — a hidden gem",
                  evidence: {},
                },
              ],
            },
          ],
        }),
      );
      return;
    }

    if (method === "GET" && path === "/watchlist") {
      await route.fulfill(json([mockTitle]));
      return;
    }

    if (method === "GET" && path.startsWith("/titles/search")) {
      await route.fulfill(json([mockTitle]));
      return;
    }

    if (method === "GET" && path.startsWith("/onboarding/cards")) {
      await route.fulfill(json({ items: [mockTitle, { ...mockTitle, id: "44444444-4444-4444-8444-444444444444", name: "Mock Sequel" }] }));
      return;
    }

    if (method === "GET" && path.startsWith("/catalog/status")) {
      await route.fulfill(
        json({
          title_count: 100,
          with_embeddings: 100,
          ready_for_onboarding: true,
        }),
      );
      return;
    }

    if (method === "GET" && /\/titles\/[^/]+\/similar/.test(path)) {
      await route.fulfill(json([]));
      return;
    }

    if (method === "GET" && /\/titles\/[^/]+\/where-to-watch/.test(path)) {
      await route.fulfill(
        json({
          region: "US",
          link: null,
          flatrate: [
            {
              provider_id: 8,
              name: "Netflix",
              logo_url: null,
              display_priority: 0,
            },
          ],
          free: [],
          ads: [],
          rent: [],
          buy: [],
          available: true,
          attribution: "Streaming data by JustWatch via TMDb",
          source: "mock",
        }),
      );
      return;
    }

    if (method === "GET" && /^\/titles\/[^/]+$/.test(path)) {
      await route.fulfill(
        json({
          ...mockTitle,
          credits: [
            {
              name: "Jane Director",
              credit_type: "crew",
              job: "Director",
              character: null,
              billing_order: null,
              profile_url: null,
            },
            {
              name: "Lead Actor",
              credit_type: "cast",
              job: null,
              character: "Hero",
              billing_order: 0,
              profile_url: null,
            },
          ],
          keywords: ["intimate", "character study"],
        }),
      );
      return;
    }

    if (method === "POST" && path.includes("/interactions")) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (method === "POST" && path === "/onboarding/complete") {
      await route.fulfill(
        json({
          ...mockUserComplete,
          onboarding_completed_at: "2026-01-15T12:00:00.000Z",
        }),
      );
      return;
    }

    await route.fulfill(
      json({ detail: "unmocked", method, path }, 404),
    );
  });
}
