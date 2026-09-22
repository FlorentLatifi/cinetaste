/**
 * Capture the README screenshots from a running stack (also a real end-to-end
 * smoke test: it registers, onboards and rates through the actual API).
 *
 *   # terminal 1: docker compose up        (API on :8000)
 *   # terminal 2: npm run dev              (SPA on :5173)
 *   node scripts/capture-screenshots.mjs [baseURL]
 *
 * Writes PNGs to docs/screenshots/.
 */
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "../../docs/screenshots");
const baseURL = process.argv[2] ?? "http://127.0.0.1:5173";
const viewport = { width: 1440, height: 900 };
const email = `demo_${Date.now()}@example.com`;
const password = "demo-password-123";

async function shot(page, name) {
  await page.waitForTimeout(600); // let posters and transitions settle
  await page.screenshot({ path: `${outDir}/${name}.png` });
  console.log(`captured ${name}.png`);
}

const run = async () => {
  await mkdir(outDir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });

  // 1. Landing (signed out)
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await shot(page, "01-landing");

  // 2. Register
  await page.goto(`${baseURL}/register`, { waitUntil: "networkidle" });
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').first().fill(password);
  await page.getByRole("button", { name: /create account/i }).click();
  await page
    .waitForURL((url) => !url.pathname.startsWith("/register"), { timeout: 20_000 })
    .catch(async () => {
      throw new Error(`registration did not navigate: ${await page.locator("body").innerText()}`);
    });

  // 3. Onboarding: rate enough titles to pass the gate
  await page.goto(`${baseURL}/onboarding`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /rate/i }).first().waitFor({ timeout: 20_000 });
  await shot(page, "02-onboarding");

  const ratings = ["Loved it", "Really liked it", "Liked it", "Loved it", "Really liked it", "It was ok", "Didn't like it"];
  for (const label of ratings) {
    const rate = page.getByRole("button", { name: /^Rate /i }).first();
    if (await rate.isVisible().catch(() => false)) await rate.click();
    const choice = page.getByRole("button", { name: new RegExp(`^${label}:`, "i") }).first();
    await choice.waitFor({ timeout: 10_000 });
    await choice.click();
    await page.waitForTimeout(350);
  }
  const finish = page.getByRole("button", { name: /see my recommendations|finish/i });
  if (await finish.isVisible().catch(() => false)) {
    await finish.click();
  }

  // 4. For You
  await page.waitForURL((url) => url.pathname === "/", { timeout: 30_000 });
  await page.getByRole("heading", { name: /picks matched/i }).waitFor({ timeout: 30_000 });
  // The explanations are the point of the product: keep them in frame.
  await page.locator(".fy-reasons").first().scrollIntoViewIfNeeded().catch(() => {});
  await shot(page, "03-for-you");

  // 5. Title detail
  await page.getByRole("link", { name: /open details for/i }).first().click();
  await page.waitForURL(/\/titles\//, { timeout: 20_000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.scrollTo(0, 320));
  await shot(page, "04-title-detail");

  // 6. Taste profile (the Taste tab holds the learned signals)
  await page.goto(`${baseURL}/account`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: /taste/i }).click().catch(async () => {
    await page.getByRole("button", { name: /^taste$/i }).click();
  });
  await page.waitForTimeout(800);
  await shot(page, "05-taste-profile");

  // 7. Same feed in the dark theme (both themes ship; both must read well)
  const darkPage = await browser.newPage({ viewport, deviceScaleFactor: 1, colorScheme: "dark" });
  await darkPage.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await darkPage.locator('input[type="email"]').fill(email);
  await darkPage.locator('input[type="password"]').first().fill(password);
  await darkPage.getByRole("button", { name: /sign in/i }).click();
  await darkPage.getByRole("heading", { name: /picks matched/i }).waitFor({ timeout: 30_000 });
  await darkPage.locator(".fy-reasons").first().scrollIntoViewIfNeeded().catch(() => {});
  await shot(darkPage, "06-for-you-dark");

  await browser.close();
  console.log(`\nScreenshots written to ${outDir}`);
};

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
