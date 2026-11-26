import { defineConfig, devices } from "@playwright/test";

const HARDWARE = process.env.ARMPI_E2E_HARDWARE === "1";
const POSE = process.env.E2E_POSE === "1" && !HARDWARE;
const PORT = Number(
  HARDWARE
    ? process.env.E2E_HARDWARE_PORT ?? 8767
    : POSE
      ? process.env.E2E_POSE_PORT ?? 8769
      : process.env.E2E_PORT ?? 8766,
);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // One backend/database and one control lease: run the suite serially.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      // Hardware mode is an explicit, private-gateway run. The default suite
      // never points at the robot and never includes the hardware spec. The
      // pose suite needs its own pose-scope mock backend.
      testMatch: HARDWARE
        ? /hardware\.spec\.ts/
        : POSE
          ? /(pose-takeover|task-takeover)\.spec\.ts/
          : undefined,
      testIgnore: HARDWARE
        ? undefined
        : POSE
          ? /hardware\.spec\.ts/
          : /(hardware|pose-takeover|task-takeover)\.spec\.ts/,
    },
  ],
  webServer: {
    command: HARDWARE
      ? "bash e2e/start-backend-hardware.sh"
      : "bash e2e/start-backend.sh",
    env: {
      ...process.env,
      E2E_PORT: String(PORT),
      E2E_POSE: POSE ? "1" : "",
    },
    url: `${BASE_URL}/api/health`,
    // In hardware mode always start a fresh backend with the hardware
    // environment; reusing a mock backend would silently invalidate the run.
    reuseExistingServer: !process.env.CI && !HARDWARE && !POSE,
    timeout: 60_000,
  },
});
