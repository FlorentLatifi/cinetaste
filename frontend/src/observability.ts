/**
 * Optional Sentry for the SPA.
 * Set VITE_SENTRY_DSN at build time (Vercel env). No DSN → no-op.
 */

const dsn = (import.meta.env.VITE_SENTRY_DSN as string | undefined)?.trim() || "";

export async function initFrontendObservability(): Promise<void> {
  if (!dsn) return;
  try {
    const Sentry = await import("@sentry/react");
    Sentry.init({
      dsn,
      environment: (import.meta.env.MODE as string) || "development",
      release: (import.meta.env.VITE_SENTRY_RELEASE as string | undefined) || undefined,
      tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? 0.1),
      // Keep PII out of error reports by default
      sendDefaultPii: false,
    });
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
