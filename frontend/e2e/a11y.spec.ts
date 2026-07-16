import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Static axe gate on guest routes (no API required).
 * Auth bootstrap fails closed → login/register render after loading spinner.
 */
const guestRoutes: { path: string; ready: string }[] = [
  { path: "/login", ready: "role=heading[name='Welcome back']" },
  { path: "/register", ready: "role=heading[name='Create account']" },
  { path: "/forgot-password", ready: "role=heading[name='Forgot password']" },
  { path: "/reset-password", ready: "role=heading[name='Reset password']" },
];

for (const route of guestRoutes) {
  test(`axe: ${route.path}`, async ({ page }) => {
    await page.goto(route.path);
    await page.locator(route.ready).waitFor({ state: "visible", timeout: 15_000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    expect(results.violations, formatViolations(results.violations)).toEqual([]);
  });
}

test("high contrast toggle updates document", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("heading", { name: "Welcome back" }).waitFor();

  const toggle = page.getByRole("button", { name: /high contrast/i });
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-contrast", "high");
  await expect(page.getByRole("button", { name: /standard contrast/i })).toBeVisible();
});

test("forced-colors mode keeps login usable", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/login");
  await page.getByRole("heading", { name: "Welcome back" }).waitFor();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /create an account/i })).toBeVisible();
});

function formatViolations(
  violations: { id: string; impact?: string | null; help: string; nodes: { target: unknown[] }[] }[],
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
