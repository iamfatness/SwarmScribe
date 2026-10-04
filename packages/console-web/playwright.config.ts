import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against the real console (e2e/harness/serve.py) serving dist/, with
// an in-memory Entra ID and two in-memory leaders. Tests share one database and reset it,
// so they run one at a time. Build first: npm run build.
const harness =
  process.env.E2E_HARNESS_COMMAND ?? "python -m uv run python packages/console-web/e2e/harness/serve.py";

export default defineConfig({
  testDir: "e2e/tests",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    // Must be the console's public_url exactly: the CSRF check compares Origin with it.
    baseURL: "http://localhost:8900",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: harness,
    cwd: "../..",
    url: "http://localhost:8900/sign-in",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
