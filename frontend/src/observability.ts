/**
 * Optional Sentry for the SPA.
 * Set VITE_SENTRY_DSN at build time (Vercel env). No DSN → no-op.
 */

import type { BrowserOptions } from "@sentry/react";

const dsn = (import.meta.env.VITE_SENTRY_DSN as string | undefined)?.trim() || "";

/**
 * Header, cookie and query-parameter names that can carry an IP address or a
 * user's identity (X-Forwarded-For, X-Real-IP, Remote-Addr, Via, X-User...).
 * Sentry v10 withheld them under `sendDefaultPii: false`; v11 no longer ships
 * the list. Keys and tokens are filtered out by the SDK regardless.
 */
export const PII_KEY_SNIPPETS = ["forwarded", "-ip", "remote-", "via", "-user"];

/**
 * Keep personal data out of error reports.
 *
 * Sentry v11 removed `sendDefaultPii`. Its replacement, `dataCollection`,
 * collects the user's IP address, cookies, headers, request bodies and query
 * parameters unless told otherwise, so deleting the old flag would have
 * started sending them. These are the values @sentry/core 10 applied for
 * `sendDefaultPii: false` (`defaultPiiToCollectionOptions`), limited to what a
 * browser SDK collects (its database and AI settings have nothing to act on here).
 */
export const DATA_COLLECTION: NonNullable<BrowserOptions["dataCollection"]> = {
  userInfo: false, // never infer or attach the user's IP address
  cookies: { deny: PII_KEY_SNIPPETS },
  httpHeaders: { deny: PII_KEY_SNIPPETS },
  urlQueryParams: { deny: PII_KEY_SNIPPETS },
  httpBodies: [],
};

export function sentryOptions(env: {
  dsn: string;
  mode?: string;
  release?: string;
  tracesSampleRate?: string | number;
}): BrowserOptions {
  return {
    dsn: env.dsn,
    environment: env.mode || "development",
    release: env.release || undefined,
    tracesSampleRate: Number(env.tracesSampleRate ?? 0.1),
    dataCollection: DATA_COLLECTION,
  };
}

export async function initFrontendObservability(): Promise<void> {
  if (!dsn) return;
  try {
    const Sentry = await import("@sentry/react");
    Sentry.init(
      sentryOptions({
        dsn,
        mode: import.meta.env.MODE as string,
        release: import.meta.env.VITE_SENTRY_RELEASE as string | undefined,
        tracesSampleRate: import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE as string | undefined,
      }),
    );
  } catch {
    // Package missing or blocked — app continues without crash reporting
  }
}

export function captureFrontendError(error: unknown, context?: Record<string, unknown>): void {
  if (!dsn) return;
  void import("@sentry/react")
    .then((Sentry) => {
      Sentry.withScope((scope) => {
        if (context) {
          for (const [k, v] of Object.entries(context)) {
            scope.setExtra(k, v);
          }
        }
        if (error instanceof Error) {
          Sentry.captureException(error);
        } else {
          Sentry.captureMessage(String(error));
        }
      });
    })
    .catch(() => {
      /* ignore */
    });
}
