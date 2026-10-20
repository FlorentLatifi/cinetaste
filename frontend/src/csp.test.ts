import { describe, expect, it } from "vitest";
import indexHtml from "../index.html?raw";
import vercelJson from "../vercel.json?raw";

/**
 * The production CSP allows exactly one inline script, by hash: the snippet
 * in index.html that applies the theme before first paint. Edit that snippet
 * without updating vercel.json and production silently blocks it: no error
 * anywhere, just the wrong theme and contrast on every page load.
 */

function inlineScripts(html: string): string[] {
  return [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `sha256-${btoa(String.fromCharCode(...new Uint8Array(digest)))}`;
}

function cspFromVercel(): string {
  const config = JSON.parse(vercelJson) as {
    headers: { source: string; headers: { key: string; value: string }[] }[];
  };
  const header = config.headers
    .flatMap((h) => h.headers)
    .find((h) => h.key.toLowerCase() === "content-security-policy");
  if (!header) throw new Error("vercel.json sets no Content-Security-Policy");
  return header.value;
}

describe("Content-Security-Policy", () => {
  it("allows every inline script in index.html by its exact hash", async () => {
    const scripts = inlineScripts(indexHtml);
    expect(scripts.length).toBeGreaterThan(0);
    const csp = cspFromVercel();
    for (const script of scripts) {
      expect(csp, "update the sha256 in vercel.json's script-src").toContain(
        `'${await sha256(script)}'`,
      );
    }
  });

  it("never falls back to unsafe-inline or unsafe-eval for scripts", () => {
    const scriptSrc = cspFromVercel()
      .split(";")
      .map((d) => d.trim())
      .find((d) => d.startsWith("script-src"));
    expect(scriptSrc).toBeDefined();
    expect(scriptSrc).not.toContain("unsafe-inline");
    expect(scriptSrc).not.toContain("unsafe-eval");
  });
});
