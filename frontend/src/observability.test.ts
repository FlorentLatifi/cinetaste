import { BrowserClient, defaultStackParser, type BrowserOptions } from "@sentry/react";
import { describe, expect, it } from "vitest";
import { PII_KEY_SNIPPETS, sentryOptions } from "./observability";

const DSN = "https://public@o0.ingest.sentry.io/0";

/** The real v11 browser client, with a transport that sends nothing. */
function clientFor(options: BrowserOptions): BrowserClient {
  return new BrowserClient({
    ...options,
    transport: () => ({ send: async () => ({}), flush: async () => true }),
    stackParser: defaultStackParser,
    integrations: [],
  });
}

const deny = { deny: PII_KEY_SNIPPETS };

describe("Sentry keeps personal data out of error reports", () => {
  it("resolves to what v10 sent with sendDefaultPii: false", () => {
    const collected = clientFor(sentryOptions({ dsn: DSN })).getDataCollectionOptions();

    expect(collected.userInfo).toBe(false);
    expect(collected.httpBodies).toEqual([]);
    expect(collected.cookies).toEqual(deny);
    expect(collected.urlQueryParams).toEqual(deny);
    expect(collected.httpHeaders).toEqual({ request: deny, response: deny });
  });

  it("never lets Sentry infer the user's IP address", () => {
    const client = clientFor(sentryOptions({ dsn: DSN }));
    expect(client.getOptions()._metadata?.sdk?.settings?.infer_ip).toBe("never");
  });

  it("would collect all of it without these options (why they exist)", () => {
    const client = clientFor({ dsn: DSN });
    expect(client.getDataCollectionOptions().userInfo).toBe(true);
    expect(client.getOptions()._metadata?.sdk?.settings?.infer_ip).toBe("auto");
  });
});

describe("sentryOptions", () => {
  it("passes the build settings through", () => {
    const options = sentryOptions({
      dsn: DSN,
      mode: "production",
      release: "abc123",
      tracesSampleRate: "0.25",
    });
    expect(options).toMatchObject({
      dsn: DSN,
      environment: "production",
      release: "abc123",
      tracesSampleRate: 0.25,
    });
  });

  it("defaults the environment and trace sampling, and leaves no empty release", () => {
    const options = sentryOptions({ dsn: DSN, mode: "", release: "" });
    expect(options.environment).toBe("development");
    expect(options.tracesSampleRate).toBe(0.1);
    expect(options.release).toBeUndefined();
  });
});
