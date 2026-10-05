import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// `npm run screens`: the same harness and browser as the end-to-end tests, but it runs
// e2e/screens/capture.spec.ts, which only takes screenshots (into screens/) for a person to
// look at. Kept out of `npm run e2e` so the test run stays a test run.
export default defineConfig({
  ...base,
  testDir: "e2e/screens",
  retries: 0,
  reporter: "list",
});
