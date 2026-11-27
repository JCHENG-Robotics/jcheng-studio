import { defineConfig } from "vitest/config";

// `e2e/` holds Playwright specs (`@playwright/test`), not vitest suites.
// Without this include, `vitest run` tries to execute them and fails to collect.
export default defineConfig({
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    passWithNoTests: true,
  },
});
