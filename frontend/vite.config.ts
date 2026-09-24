/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    // Unit tests for the pure logic — parsing, scoring, formatting. Behaviour
    // that needs a browser stays in e2e/, which Playwright runs; excluded here
    // because those files import `test` from @playwright/test and vitest would
    // otherwise try to run them.
    include: ["src/**/*.test.ts"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    environment: "node",
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      // Only the modules these tests are responsible for. Components and pages
      // are covered by the Playwright suite, and counting them here would turn
      // one number into a blend of two different kinds of testing.
      include: [
        "src/lib/**/*.ts",
        "src/features/taste/**/*.ts",
        "src/api/tokenStore.ts",
      ],
      // Set from a measured run, a little under where each sits, so a real
      // regression trips them and ordinary churn does not.
      thresholds: {
        statements: 95,
        branches: 92,
        functions: 95,
        lines: 95,
      },
    },
  },
});
