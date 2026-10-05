import { expect, test } from "@playwright/test";
import { installApiMock, mockSecondPick, mockTitle } from "./helpers/mockApi";

/**
 * Behavioral smoke tests against Playwright API mocks (no real backend).
 */
test("Unknown route shows real 404 page", async ({ page }) => {
  await page.goto("/this-route-does-not-exist");
  await expect(
    page.getByRole("heading", { name: /doesn’t exist|doesn't exist/i }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /^Home$/i })).toBeVisible();
});

test("Landing: the first action is trying it, not signing up", async ({ page }) => {
  await installApiMock(page, { signedOut: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Rate three films. Get tonight’s picks. Every pick explained." })).toBeVisible();
  await expect(page.getByRole("link", { name: /Create account/i })).toBeVisible();
  await page.getByRole("link", { name: /Try it now — no sign-up/i }).click();
  await expect(page).toHaveURL(/\/try$/);
  await expect(page.getByRole("heading", { name: "Rate what you know" })).toBeVisible();
});

test("Register: password show toggle and strength meter", async ({ page }) => {
  await page.goto("/register");
  await page.getByRole("heading", { name: "Create account" }).waitFor();

  const password = page.locator('input[autocomplete="new-password"]');
  await expect(password).toBeVisible();
  await password.fill("short");
  await expect(page.getByText("Strength:")).toBeVisible();
  await expect(page.locator(".password-strength-label strong")).toHaveText(
    /Weak|Fair/,
  );

  await page.getByRole("button", { name: "Show password" }).click();
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(password).toHaveAttribute("type", "password");

  await password.fill("Str0ng!pass");
  await expect(page.locator(".password-strength-label strong")).toHaveText(
    /Good|Strong/,
  );
});

test("Onboarding: skip advances to next card", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: false });
  await page.goto("/onboarding");
  await page.getByRole("heading", { name: "Rate what you know" }).waitFor();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();

  await page
    .getByRole("button", { name: new RegExp(`Haven't seen ${mockTitle.name}`, "i") })
    .click();
  await expect(page.getByRole("heading", { name: "Mock Sequel" })).toBeVisible({
    timeout: 5_000,
  });
});

test("Onboarding: Rate opens scale and records a rating", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: false });
  await page.goto("/onboarding");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: `Rate ${mockTitle.name}` }).click();
  await expect(page.getByText("How was it for you?")).toBeVisible();
  await page.getByRole("button", { name: /Really liked it:/i }).click();
  // Progress should reflect at least one rating
  await expect(page.getByText(/of 6 rated/i)).toBeVisible();
  await expect(page.locator(".ob-progress-count strong")).toHaveText("1");
});

test("For You: several picks in a row, and a pass can be undone", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: /Picks matched/i }).waitFor();

  // More than one pick is on the page at once.
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();
  await expect(page.getByRole("heading", { name: mockSecondPick.name })).toBeAttached();
  await expect(page.getByText("/ 2")).toBeVisible();

  await page.getByRole("button", { name: `Not for me — ${mockTitle.name}` }).click();
  await expect(page.getByText(new RegExp(`Marked not interested · ${mockTitle.name}`))).toBeVisible();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: mockSecondPick.name })).toBeVisible();

  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();
});

test("For You: arrow keys browse and W saves the pick in view", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: "Next pick" }).click();
  await expect(page.locator(".pick-position strong")).toHaveText("2");
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".pick-position strong")).toHaveText("1");

  await page.keyboard.press("w");
  await expect(page.getByText(new RegExp(`Saved to watchlist · ${mockTitle.name}`))).toBeVisible();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toHaveCount(0);
});

test("For You: Seen it opens a rating row and records the rating", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: `Seen it — rate ${mockTitle.name}` }).click();
  await expect(page.getByText("How was it?")).toBeVisible();
  await page.getByRole("button", { name: `Loved it — ${mockTitle.name}` }).click();
  await expect(page.getByText(new RegExp(`Loved it · ${mockTitle.name}`))).toBeVisible();
});

test("For You: double-click only posts one interaction", async ({ page }) => {
  const mock = await installApiMock(page, {
    onboardingComplete: true,
    interactionDelayMs: 250,
  });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  const pass = page.getByRole("button", { name: `Not for me — ${mockTitle.name}` });
  await pass.dblclick();
  await expect(page.getByText(new RegExp(`Marked not interested · ${mockTitle.name}`))).toBeVisible({
    timeout: 10_000,
  });
  // busy/exiting guards must collapse double activation to a single POST
  expect(mock.interactionPosts()).toBe(1);
});

test("For You: dead session on rating returns guest to landing", async ({ page }) => {
  await installApiMock(page, {
    onboardingComplete: true,
    sessionDeadOnInteraction: true,
  });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: `Not for me — ${mockTitle.name}` }).click();
  // Session cleared → RootRoute renders public LandingPage
  await expect(
    page.getByRole("heading", { name: "Rate three films. Get tonight’s picks. Every pick explained." }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("link", { name: /Try it now/i })).toBeVisible();
});

test("App shell: mobile bottom nav exposes primary destinations", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: /Picks matched/i }).waitFor();

  // Bottom nav is CSS-shown only ≤720px; display:none keeps it out of a11y tree on desktop.
  const primary = page.getByRole("navigation", { name: "Primary" });
  await expect(primary).toBeVisible();
  await expect(primary.getByRole("link", { name: "For You" })).toBeVisible();
  await expect(primary.getByRole("link", { name: "Search" })).toBeVisible();
  await expect(primary.getByRole("link", { name: "Watchlist" })).toBeVisible();
  await expect(primary.getByRole("link", { name: "History" })).toBeVisible();
  await expect(primary.getByRole("link", { name: "Account" })).toBeVisible();

  await primary.getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/search/);
  await expect(page.getByRole("heading", { name: /Search/i })).toBeVisible();
});

test("For You: empty slate shows recovery CTAs", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true, forYouEmpty: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /been through every pick/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /New picks/i })).toBeVisible();
});

test("For You: load error offers Try again", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true, forYouError: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Something went wrong/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Try again/i })).toBeVisible();
});

test("History: Clear removes row after mock interaction", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/history");
  await page.getByRole("heading", { name: "History" }).waitFor();

  await expect(
    page.getByRole("heading", { name: mockTitle.name, exact: true }),
  ).toBeVisible();
  // Infinite scroll may already have loaded page 2 — clear only the first title.
  await page
    .getByRole("button", { name: `Clear status for ${mockTitle.name}`, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: mockTitle.name, exact: true }),
  ).toHaveCount(0);
});

test("History: filter chips update URL and list", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/history");
  await page.getByRole("heading", { name: "History" }).waitFor();

  // Mock returns a liked title; Liked filter keeps it, Passed empties.
  await page.getByRole("button", { name: "Liked", exact: true }).click();
  await expect(page).toHaveURL(/state=like/);
  await expect(
    page.getByRole("heading", { name: mockTitle.name, exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Passed", exact: true }).click();
  await expect(page).toHaveURL(/state=dislike/);
  await expect(page.getByText(/No titles marked/i)).toBeVisible();

  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page).not.toHaveURL(/state=/);
  await expect(
    page.getByRole("heading", { name: mockTitle.name, exact: true }),
  ).toBeVisible();
});

test("History: infinite scroll / Load more appends next page", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/history");
  await page.getByRole("heading", { name: "History" }).waitFor();

  await expect(
    page.getByRole("heading", { name: mockTitle.name, exact: true }),
  ).toBeVisible();

  // Sentinel may auto-load when already in view; otherwise click Load more.
  const second = page.getByRole("heading", { name: "Mock Classic II", exact: true });
  try {
    await second.waitFor({ state: "visible", timeout: 3_000 });
  } catch {
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(second).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
});

test("Account: tabs isolate taste import flow", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");
  await page.getByRole("heading", { name: "Your profile" }).waitFor();
  await expect(page.getByRole("tab", { name: "Profile" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByRole("tab", { name: "Taste" }).click();
  await expect(page).toHaveURL(/tab=taste/);
  await expect(page.getByRole("heading", { name: "Your taste" })).toBeVisible();
  await page.getByRole("tab", { name: "Appearance" }).click();
  await expect(page.getByLabel("Color theme")).toBeVisible();
  await page.getByLabel("Color theme").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("Account: arrow keys move between tabs", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");
  await page.getByRole("tab", { name: "Profile" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Taste" })).toBeFocused();
  await expect(page).toHaveURL(/tab=taste/);
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Danger zone" })).toBeFocused();
  await expect(page).toHaveURL(/tab=danger/);
});

test("Account: open snapshot previews export JSON", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account?tab=taste");
  await page.getByRole("heading", { name: "Your taste" }).waitFor();

  const snapshot = {
    schema: "cinetaste.taste_snapshot.v1",
    exported_at: "2026-01-15T12:00:00.000Z",
    profile_version: 2,
    updated_at: null,
    has_vector: true,
    feature_count: 2,
    anchor_count: 0,
    likes: [
      { key: "genre:drama", family: "genre", label: "Drama", weight: 1.5 },
    ],
    dislikes: [
      { key: "genre:horror", family: "genre", label: "Horror", weight: -1 },
    ],
    anchors: [],
  };

  await page.getByLabel("Open taste snapshot JSON file").setInputFiles({
    name: "taste.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(snapshot)),
  });

  const preview = page.getByLabel("Imported snapshot preview");
  await expect(preview).toBeVisible();
  await expect(preview.getByText("Snapshot preview")).toBeVisible();
  await expect(preview.getByText("Lean toward (file)")).toBeVisible();
  await expect(preview.getByText("Drama", { exact: true })).toBeVisible();
  await expect(preview.getByText("Horror", { exact: true })).toBeVisible();
  await preview.getByRole("button", { name: /merge into my profile/i }).click();
  // Confirm dialog (focus trap)
  await expect(page.getByRole("dialog", { name: /merge taste snapshot/i })).toBeVisible();
  await page.getByRole("button", { name: "Merge", exact: true }).click();
  await expect(page.getByText(/Merged 2 signals/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /clear imported snapshot/i })).toBeVisible();
  await page.getByRole("button", { name: /clear imported snapshot/i }).click();
  await expect(page.getByRole("dialog", { name: /clear imported snapshot/i })).toBeVisible();
  await page.getByRole("button", { name: "Clear import", exact: true }).click();
  await expect(page.getByText(/Cleared merged snapshot overlay/i)).toBeVisible();
});



test("For You: malformed item with null poster does not crash", async ({
  page,
}) => {
  const handle = await installApiMock(page, { onboardingComplete: true });
  // Override For You response with null poster
  await page.route("**/api/v1/recommendations/for-you**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            title: {
              ...mockTitle,
              poster_url: null,
              poster_path: null,
              backdrop_path: null,
            },
            score: 0.85,
            reasons: [
              {
                code: "shared_genre",
                message: "Fits your taste",
                evidence: {},
              },
            ],
          },
        ],
      }),
    });
  });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();
  // No crash = page rendered without poster
  await expect(
    page.getByRole("button", { name: /Want to watch —/i }).first(),
  ).toBeVisible();
});

test("Title detail: Watched opens rate panel", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto(`/titles/${mockTitle.id}`);
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: /Mark .* as watched and rate/i }).click();
  await expect(page.getByText("How was it?")).toBeVisible();
  await page.getByRole("button", { name: `Rate ${mockTitle.name}: Really liked it` }).click();
  await expect(page.getByText(`Really liked it · ${mockTitle.name}`)).toBeVisible();
});

test("Account: unconfirmed email offers a confirmation link", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true, emailUnverified: true });
  await page.goto("/account");

  await expect(page.getByText("· not confirmed")).toBeVisible();
  await page.getByRole("button", { name: "Send confirmation link" }).click();
  await expect(page.getByRole("status")).toContainText(/verification link has been sent/i);
});

test("Account: a confirmed email shows no prompt", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");

  await expect(page.getByText("· confirmed")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send confirmation link" }),
  ).toHaveCount(0);
});

test("Verify email: a good link confirms the address", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true, emailUnverified: true });
  await page.goto("/verify-email?token=a-valid-looking-token-value");

  await expect(page.getByRole("heading", { name: "Email confirmed" })).toBeVisible();
});

test("Verify email: an expired link explains itself", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true, verifyEmailFails: true });
  await page.goto("/verify-email?token=a-stale-token-value-here");

  await expect(page.getByRole("heading", { name: "That link did not work" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(/expired/i);
});

test("Verify email: a link with no token says so", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/verify-email");

  await expect(page.getByRole("alert")).toContainText(/missing its token/i);
});

test("Enforced verification: nothing but check-your-inbox until it's done", async ({
  page,
}) => {
  await installApiMock(page, {
    onboardingComplete: true,
    emailUnverified: true,
    verificationEnforced: true,
  });
  await page.goto("/history");

  // Known from the session itself, so no gated request has to fail first.
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send a new link" })).toBeVisible();
  await page.getByRole("button", { name: "Send a new link" }).click();
  await expect(page.getByRole("status")).toContainText(/verification link has been sent/i);

  // The account page stays reachable, to delete a mistyped address.
  await page.getByRole("link", { name: "Delete this account" }).click();
  await expect(page).toHaveURL(/\/account$/);
});

test("Guest: rate three, get a row of picks, and the saves follow you to sign-up", async ({
  page,
}) => {
  await installApiMock(page, { signedOut: true });
  await page.goto("/try");
  await page.getByRole("heading", { name: "Rate what you know" }).waitFor();
  await expect(page.getByText(/no account needed/i)).toBeVisible();

  for (const name of [mockTitle.name, "Mock Sequel", "Mock Thriller"]) {
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("button", { name: `Rate ${name}` }).click();
    await page.getByRole("button", { name: /Really liked it:/i }).click();
  }

  await page.getByRole("button", { name: /Show my picks/i }).click();
  await expect(page.getByRole("heading", { name: /what we.d watch/i })).toBeVisible();
  await expect(page.getByText(`Because you liked ${mockTitle.name}`).first()).toBeVisible();

  await page.getByRole("button", { name: `Want to watch — ${mockSecondPick.name}` }).click();
  await expect(page.getByText("1 saved.")).toBeVisible();

  await page.getByRole("link", { name: "Keep my picks" }).click();
  await expect(page).toHaveURL(/\/register$/);
  await expect(page.getByText(/3 guest answers and 1 saved pick come with you/)).toBeVisible();
});

test("Onboarding: guest answers carry over, and can be discarded", async ({ page }) => {
  await page.addInitScript(
    ([draft]) => window.localStorage.setItem("cinetaste.guestDraft.v1", draft),
    [
      JSON.stringify({
        savedAt: Date.now(),
        saved: [],
        reactions: [
          { title_id: mockTitle.id, action: "rate_4" },
          { title_id: "44444444-4444-4444-8444-444444444444", action: "rate_3" },
        ],
      }),
    ],
  );
  await installApiMock(page, { onboardingComplete: false });
  await page.goto("/onboarding");

  await expect(page.getByText(/2 answers carried over/)).toBeVisible();
  await expect(page.locator(".ob-progress-count strong")).toHaveText("2");
  // Answered cards are not asked again.
  await expect(page.getByRole("heading", { name: "Mock Thriller" })).toBeVisible();

  await page.getByRole("button", { name: /Start fresh/ }).click();
  await expect(page.locator(".ob-progress-count strong")).toHaveText("0");
  await expect(page.getByText(/carried over/)).toHaveCount(0);
});

test("Privacy: reachable signed out and says what is stored", async ({ page }) => {
  // A policy you can only read after registering is not a policy.
  await page.goto("/privacy");

  await expect(page.getByRole("heading", { name: "Privacy", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What is stored" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What you can do" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to CineTaste" })).toBeVisible();
});

test("TMDb attribution appears on the landing page and behind sign-in", async ({ page }) => {
  // Their terms require this wording wherever their data is shown.
  const required = /uses the TMDB API but is not endorsed or certified by TMDB/i;

  await page.goto("/");
  await expect(page.getByText(required)).toBeVisible();
  await expect(page.getByRole("link", { name: "Privacy" })).toBeVisible();

  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");
  await expect(page.getByText(required)).toBeVisible();
});

test("A failed load offers Try again, and the retry works", async ({ page }) => {
  // The watchlist used to render a red line and nothing else — no button, no
  // link, nothing to do but navigate away.
  await installApiMock(page, { onboardingComplete: true, failOnce: ["/watchlist"] });
  await page.goto("/watchlist");

  await expect(page.getByRole("heading", { name: /Couldn.t load your watchlist/i })).toBeVisible();
  await expect(page.getByText("The server is having a moment.")).toBeVisible();

  await page.getByRole("button", { name: "Try again" }).click();

  await expect(page.getByRole("heading", { name: /Couldn.t load/i })).toHaveCount(0);
});

test("A failed search offers Try again", async ({ page }) => {
  await installApiMock(page, {
    onboardingComplete: true,
    failOnce: ["/titles/search"],
  });
  await page.goto("/search?q=mock");

  await expect(page.getByRole("heading", { name: /Couldn.t load those results/i })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: /Couldn.t load/i })).toHaveCount(0);
});

test("A slow first request explains itself instead of spinning silently", async ({ page }) => {
  // The API sleeps on its free tier; a cold start is about fifty seconds, and
  // a bare spinner for that long reads as broken.
  await installApiMock(page, { onboardingComplete: true, refreshDelayMs: 7000 });
  await page.goto("/");

  await expect(page.getByText(/Waking the server/i)).toBeVisible({ timeout: 10_000 });
});

test("Signing out in one tab signs the other out too", async ({ page, context }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "Details" })).toBeVisible();

  const second = await context.newPage();
  await installApiMock(second, { onboardingComplete: true });
  await second.goto("/account");
  await expect(second.getByRole("heading", { name: "Details" })).toBeVisible();

  // Sign out in the first tab. The second shares the origin's localStorage, so
  // it hears about it without making a request of its own.
  await page.getByRole("button", { name: /Sign out/i }).click();

  await expect(second).toHaveURL(/\/login$/, { timeout: 10_000 });
  await second.close();
});
