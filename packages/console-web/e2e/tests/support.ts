import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

export { expect };

/** The harness's control server (e2e/harness/serve.py); never part of the console. */
export const CONTROL = "http://127.0.0.1:8901";
export type Persona = "viewer" | "operator" | "admin";

const ENTRA = "https://login.microsoftonline.com/";

export async function resetWorld(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/reset`);
  expect(answer.ok()).toBe(true);
}

export async function setLeaderMode(
  request: APIRequestContext,
  name: string,
  mode: "ok" | "down",
): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/leaders/${name}/mode`, { data: { mode } });
  expect(answer.ok()).toBe(true);
}

export async function endAllSessions(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/sessions/expire`);
  expect(answer.ok()).toBe(true);
}

/**
 * The whole browser sign-in: the console's sign-in page, its redirect to Entra ID (stopped
 * here and answered by the harness for `persona`), and the callback, which refreshes to the
 * page the person asked for.
 */
export async function signIn(page: Page, persona: Persona, path = "/"): Promise<void> {
  await page.route(`${ENTRA}**`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<p>Microsoft sign-in</p>" }),
  );
  await page.goto(path);
  await expect(page).toHaveURL(/\/sign-in/);
  const toEntra = page.waitForRequest((request) => request.url().startsWith(ENTRA));
  await page.getByRole("link", { name: "Sign in with Microsoft Entra ID" }).click();
  const location = (await toEntra).url();
  const answer = await page.request.post(`${CONTROL}/control/authorize`, { data: { location, persona } });
  expect(answer.ok()).toBe(true);
  const { callback } = (await answer.json()) as { callback: string };
  await page.goto(callback);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** Zero WCAG 2.1 A/AA violations on the page as it is now. */
export async function expectAccessible(page: Page, context: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const found = results.violations.map(
    (violation) =>
      `${context}: ${violation.id} at ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`,
  );
  expect(found).toEqual([]);
}

export const THEMES = ["light", "dark"] as const;

/**
 * Every test fails on a Content Security Policy violation or an uncaught page error, and
 * starts from a freshly reset console.
 */
export const test = base.extend<{ guard: undefined }>({
  guard: [
    async ({ page, request }, use) => {
      const problems: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error" && message.text().includes("Content Security Policy")) {
          problems.push(message.text());
        }
      });
      page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
      await resetWorld(request);
      await use(undefined);
      expect(problems).toEqual([]);
    },
    { auto: true },
  ],
});
