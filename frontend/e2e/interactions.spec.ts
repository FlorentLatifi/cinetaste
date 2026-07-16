import { expect, test } from "@playwright/test";
import { installApiMock, mockTitle } from "./helpers/mockApi";

/**
 * Behavioral smoke tests against Playwright API mocks (no real backend).
 */
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
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();

  await page.getByRole("button", { name: "Passed", exact: true }).click();
  await expect(page).toHaveURL(/state=dislike/);
  await expect(page.getByText(/No titles marked/i)).toBeVisible();

  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page).not.toHaveURL(/state=/);
  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();
});

test("History: infinite scroll / Load more appends next page", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/history");
  await page.getByRole("heading", { name: "History" }).waitFor();

  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();

  // Sentinel may auto-load when already in view; otherwise click Load more.
  const second = page.getByRole("heading", { name: "Mock Classic II" });
  try {
    await second.waitFor({ state: "visible", timeout: 3_000 });
  } catch {
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(second).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
});

test("Account: open snapshot previews export JSON", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto("/account");
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
