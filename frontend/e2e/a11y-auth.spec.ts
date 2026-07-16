import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { installApiMock, mockTitle } from "./helpers/mockApi";

/**
 * Authenticated surfaces — session + data via Playwright route mocks (no API).
 */
const authRoutes: { path: string; ready: string | RegExp; setup?: "search" }[] = [
  {
    path: "/",
    ready: /Picks matched to your taste|Picks matched to you/,
  },
  {
    path: "/search",
    ready: "role=heading[name='Search the catalog']",
    setup: "search",
  },
  {
    path: "/watchlist",
    ready: "role=heading[name='Watchlist']",
  },
  {
    path: "/history",
    ready: "role=heading[name='History']",
  },
  {
    path: "/account",
    ready: "role=heading[name='Your profile']",
  },
  {
    path: `/titles/${mockTitle.id}`,
    ready: "role=heading[name='Mock Classic']",
  },
];

for (const route of authRoutes) {
  test(`axe (auth): ${route.path}`, async ({ page }) => {
    await installApiMock(page, { onboardingComplete: true });
    await page.goto(route.path);

    if (typeof route.ready === "string") {
      await page.locator(route.ready).waitFor({ state: "visible", timeout: 15_000 });
    } else {
      await page.getByRole("heading", { name: route.ready }).waitFor({
        state: "visible",
        timeout: 15_000,
      });
    }

    // Search: run a query so results markup is in the a11y tree
    if (route.setup === "search") {
      await page.getByLabel(/search movies/i).fill("mock");
      await page.getByRole("button", { name: /^search$/i }).click();
      await page.getByRole("heading", { name: /results/i }).waitFor({
        state: "visible",
        timeout: 10_000,
      });
    }

    // Shell chrome present
    await expect(page.getByRole("link", { name: /skip to main content/i })).toBeAttached();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    expect(results.violations, formatViolations(results.violations)).toEqual([]);
  });
}

test("axe (auth): /onboarding", async ({ page }) => {
  await installApiMock(page, { onboardingComplete: false });
  await page.goto("/onboarding");
  await page.getByRole("heading", { name: "Rate what you know" }).waitFor({
    state: "visible",
    timeout: 15_000,
  });
  await page.getByRole("heading", { name: "Mock Classic" }).waitFor({
    state: "visible",
    timeout: 10_000,
  });

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  expect(results.violations, formatViolations(results.violations)).toEqual([]);
});

function formatViolations(
  violations: {
    id: string;
    impact?: string | null;
    help: string;
    nodes: { target: unknown[] }[];
  }[],
): string {
  if (!violations.length) return "";
  return violations
    .map((v) => {
      const targets = v.nodes
        .slice(0, 5)
        .map((n) => `  - ${JSON.stringify(n.target)}`)
        .join("\n");
      return `[${v.impact ?? "unknown"}] ${v.id}: ${v.help}\n${targets}`;
    })
    .join("\n\n");
}
