import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

export { expect };

/** The harness's control server (e2e/harness/serve.py); never part of the console. */
export const CONTROL = "http://127.0.0.1:8901";
/** The console's origin: playwright.config.ts's baseURL. */
const CONSOLE = "http://localhost:8900";
export type Persona = "viewer" | "operator" | "admin";

const ENTRA = "https://login.microsoftonline.com/";

export async function resetWorld(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/reset`);
  expect(answer.ok()).toBe(true);
}

export async function setLeaderMode(
  request: APIRequestContext,
  name: string,
  mode: "ok" | "down" | "revoked",
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
 *
 * The redirect is stopped at the console's own answer, not at Entra ID's address: Playwright
 * never offers a redirect's second request to a route, so a route on the Entra ID address let
 * the browser load the real login.microsoftonline.com, whose script then navigated the page
 * again and at times overtook the callback.
 */
export async function signIn(page: Page, persona: Persona, path = "/"): Promise<void> {
  const redirect = { status: 0, location: "" };
  await page.route("**/auth/login?**", async (route) => {
    const answer = await route.fetch({ maxRedirects: 0 });
    redirect.status = answer.status();
    redirect.location = answer.headers()["location"] ?? "";
    // The browser keeps the console's cookies (the pending sign-in's) and goes no further.
    const cookies = answer
      .headersArray()
      .filter((header) => header.name.toLowerCase() === "set-cookie")
      .map((header) => header.value);
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      headers: { "set-cookie": cookies.join("\n") },
      body: "<p>Microsoft sign-in</p>",
    });
  });
  await page.goto(path);
  await expect(page).toHaveURL(/\/sign-in/);
  await page.getByRole("link", { name: "Continue with Microsoft" }).click();
  await expect(page.getByText("Microsoft sign-in")).toBeVisible();
  expect(redirect.status).toBe(302);
  expect(redirect.location.startsWith(ENTRA)).toBe(true);
  const { location } = redirect;
  const answer = await page.request.post(`${CONTROL}/control/authorize`, { data: { location, persona } });
  expect(answer.ok()).toBe(true);
  const { callback } = (await answer.json()) as { callback: string };
  await page.goto(callback);
  // The brand link is in the signed-in frame at every width (Sign out is behind the Menu
  // button on a narrow screen), and nowhere on the sign-in page.
  await expect(page.getByRole("link", { name: "SwarmScribe console" })).toBeVisible();
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
 * Has the person's Theme choice already made when the page opens. The console is dark for
 * everyone until they choose (the system's setting is not followed), so a test of the light
 * theme chooses Light, as a person would. A choice made later, in the page, is kept.
 */
export async function chooseTheme(page: Page, theme: "light" | "dark" | "system"): Promise<void> {
  await page.addInitScript((choice) => {
    const key = "swarmscribe-console-theme";
    if (localStorage.getItem(key) === null) localStorage.setItem(key, choice);
  }, theme);
}

/**
 * Every test fails on a Content Security Policy violation, an uncaught page error or a
 * browser request to anything but the console (the tests must never depend on the
 * Internet), and starts from a freshly reset console.
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
      page.on("request", (sent) => {
        const url = new URL(sent.url());
        if (/^https?:$/.test(url.protocol) && url.origin !== CONSOLE) {
          problems.push(`the browser asked the network for ${url.origin}${url.pathname}`);
        }
      });
      await resetWorld(request);
      await use(undefined);
      expect(problems).toEqual([]);
    },
    { auto: true },
  ],
});
