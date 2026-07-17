import { expect, test } from "@playwright/test";
import { installApiMock, mockTitle } from "./helpers/mockApi";

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

test("Landing: guest home shows Start free and preview", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "One poster. Your taste. Every pick explained.",
    }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /Start free/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /Get started/i })).toBeVisible();
  await page.getByRole("link", { name: /Start free/i }).click();
  await expect(page).toHaveURL(/\/register/);
  await expect(page.getByRole("heading", { name: "Create account" })).toBeVisible();
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

test("For You: Pass removes card and Undo restores it", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: /Picks matched/i }).waitFor();

  const cardTitle = page.getByRole("heading", { name: mockTitle.name });
  await expect(cardTitle).toBeVisible();

  await page.getByRole("button", { name: `Pass on ${mockTitle.name}` }).click();
  await expect(page.getByText(new RegExp(`Passed · ${mockTitle.name}`))).toBeVisible();
  await expect(cardTitle).toHaveCount(0);

  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();
});

test("For You: keyboard 1 passes the current pick", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.keyboard.press("1");
  await expect(page.getByText(new RegExp(`Passed · ${mockTitle.name}`))).toBeVisible();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toHaveCount(0);
});

test("For You: double-click Pass only posts one interaction", async ({ page }) => {
  const mock = await installApiMock(page, {
    onboardingComplete: true,
    interactionDelayMs: 250,
  });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  const pass = page.getByRole("button", { name: `Pass on ${mockTitle.name}` });
  await pass.dblclick();
  await expect(page.getByText(new RegExp(`Passed · ${mockTitle.name}`))).toBeVisible({
    timeout: 10_000,
  });
  // busy/exiting guards must collapse double activation to a single POST
  expect(mock.interactionPosts()).toBe(1);
});

test("For You: dead session on Pass returns guest to landing", async ({ page }) => {
  await installApiMock(page, {
    onboardingComplete: true,
    sessionDeadOnInteraction: true,
  });
  await page.goto("/");
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: `Pass on ${mockTitle.name}` }).click();
  // Session cleared → RootRoute renders public LandingPage
  await expect(
    page.getByRole("heading", {
      name: "One poster. Your taste. Every pick explained.",
    }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("link", { name: /Start free/i })).toBeVisible();
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
  await expect(page.getByRole("heading", { name: /No more picks/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /Browse search/i })).toBeVisible();
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

test("Title detail: Watched opens rate panel", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto(`/titles/${mockTitle.id}`);
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: /Mark .* as watched and rate/i }).click();
  await expect(page.getByText("How was it?")).toBeVisible();
  await page.getByRole("button", { name: `Rate ${mockTitle.name}: Good` }).click();
  await expect(page.getByText(`Rated Good · ${mockTitle.name}`)).toBeVisible();
});
