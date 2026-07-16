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

  await expect(page.getByRole("heading", { name: mockTitle.name })).toBeVisible();
  await page.getByRole("button", { name: `Clear status for ${mockTitle.name}` }).click();
  await expect(page.getByRole("heading", { name: mockTitle.name })).toHaveCount(0);
  await expect(page.getByText(/No history yet|No titles marked/i)).toBeVisible();
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

test("Title detail: Watched opens rate panel", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: true });
  await page.goto(`/titles/${mockTitle.id}`);
  await page.getByRole("heading", { name: mockTitle.name }).waitFor();

  await page.getByRole("button", { name: /Mark .* as watched and rate/i }).click();
  await expect(page.getByText("How was it?")).toBeVisible();
  await page.getByRole("button", { name: `Rate ${mockTitle.name}: Good` }).click();
  await expect(page.getByText(`Rated Good · ${mockTitle.name}`)).toBeVisible();
});
