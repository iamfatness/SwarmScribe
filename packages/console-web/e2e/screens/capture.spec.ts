import { mkdirSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { chooseTheme, expect, setLeaderMode, signIn, test } from "../tests/support";

// Not a test of behaviour: it photographs every screen in both themes at desktop and tablet
// width, into packages/console-web/screens/, for a person to LOOK at. jsdom and axe pass
// layouts that are plainly wrong to the eye (a cut-off column, text under a pinned cell, a
// card stretched to nothing), so a redesign is not done until these have been looked at.
// Run with: npm run screens   (the same harness as npm run e2e; build first).

const OUT = join(process.cwd(), "screens");
const WIDTHS = [1280, 900, 768] as const;
const THEMES = ["dark", "light"] as const;
/** The console's origin (playwright.config.ts's baseURL), for the requests made as the page. */
const ORIGIN = "http://localhost:8900";
/** The console tests' credential shape (console_testkit.CREDENTIAL): 43 URL-safe characters. */
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);

const PAGES: [name: string, path: string][] = [
  ["fleet", "/"],
  ["fleet-filtered", "/?label=region%3Dus"],
  ["leader-pools", "/leaders/eu-1/pools"],
  ["leader-jobs", "/leaders/eu-1/jobs"],
  ["leader-jobs-failed", "/leaders/eu-1/jobs?state=failed"],
  ["leader-locations", "/leaders/eu-1/locations"],
  ["leader-tokens", "/leaders/eu-1/tokens"],
  ["leader-consent", "/leaders/eu-1/consent"],
  ["admin-leaders", "/admin/leaders"],
  ["admin-grants", "/admin/grants"],
  ["admin-admins", "/admin/admins"],
  ["not-found", "/no-such-page"],
];

/** A screenshot named dark must be dark: check the page background before every shot. */
async function expectTheme(
  page: Page,
  theme: (typeof THEMES)[number],
): Promise<void> {
  const background = await page.evaluate(
    () => getComputedStyle(document.documentElement).backgroundColor,
  );
  const [r = 0, g = 0, b = 0] = (background.match(/\d+/g) ?? []).map(Number);
  const brightness = (r + g + b) / 3;
  expect(
    theme === "dark" ? brightness < 80 : brightness > 180,
    `${theme} page background is ${background}`,
  ).toBe(true);
}

async function settled(page: Page): Promise<void> {
  await expect(page.getByText(/^Loading/)).toHaveCount(0);
  await page.waitForLoadState("networkidle");
}

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`screens: ${theme} at ${width}px`, async ({ page, request }) => {
      test.setTimeout(240_000);
      mkdirSync(OUT, { recursive: true });
      const shot = async (name: string, fullPage = true) => {
        await expectTheme(page, theme);
        await page.screenshot({
          path: join(OUT, `${name}-${theme}-${width}.png`),
          fullPage,
        });
      };
      // Dark is the default whatever the system says; the light shots choose Light.
      await page.emulateMedia({ colorScheme: theme });
      await chooseTheme(page, theme);
      await page.setViewportSize({ width, height: 900 });

      await page.goto("/sign-in?signed_out=1");
      await expect(
        page.getByRole("link", { name: "Continue with Microsoft" }),
      ).toBeVisible();
      await shot("sign-in");

      await signIn(page, "admin");
      for (const [name, path] of PAGES) {
        await page.goto(path);
        await settled(page);
        await shot(name);
      }

      // The rail as a top bar, with its menu open (tablet width only).
      const menu = page.getByRole("button", { name: "Menu" });
      await page.goto("/");
      await settled(page);
      if (await menu.isVisible()) {
        await menu.click();
        await shot("menu-open");
      }

      // Dialogs: a confirmation, a form with its errors showing, and the one-time token.
      await page.goto("/leaders/eu-1/jobs");
      await page
        .getByRole("button", { name: /^Cancel job / })
        .first()
        .click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await shot("dialog-confirm", false);
      await page.keyboard.press("Escape");

      await page.goto("/leaders/eu-1/locations");
      await page.getByRole("button", { name: "Add location" }).click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Add location" })
        .click();
      await expect(
        page.getByRole("dialog").getByRole("alert").first(),
      ).toBeVisible();
      await shot("dialog-form-errors", false);
      await page.keyboard.press("Escape");

      await page.goto("/leaders/eu-1/tokens");
      await page.getByRole("button", { name: "Create join token" }).click();
      await shot("dialog-token-form", false);
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Create token" })
        .click();
      await expect(
        page.getByRole("textbox", { name: "Join token" }),
      ).toHaveValue(/^sst_/);
      await shot("dialog-token-shown", false);
      await page.getByRole("button", { name: "I have stored it" }).click();
      await shot("dialog-token-asking", false);
      await page.getByRole("button", { name: "I have stored it" }).click();

      await page.goto("/admin/leaders");
      await page.getByRole("button", { name: "Add a leader" }).click();
      await shot("dialog-add-leader", false);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Edit eu-1" }).click();
      await shot("dialog-edit-leader", false);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Replace credential for eu-1" }).click();
      await shot("dialog-replace-credential", false);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Remove eu-1" }).click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await shot("dialog-remove-leader", false);
      await page.keyboard.press("Escape");
      await page.goto("/admin/grants");
      await page.getByRole("button", { name: /^Remove / }).first().click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await shot("dialog-remove-role", false);
      await page.keyboard.press("Escape");

      // A viewer: what switched-off actions look like.
      await page.context().clearCookies();
      await signIn(page, "viewer", "/leaders/eu-1/jobs");
      await settled(page);
      await shot("leader-jobs-as-viewer");

      // One leader down: its card, its place in the rail, and its own page.
      await setLeaderMode(request, "us-1", "down");
      await page.goto("/");
      await expect(
        page
          .getByRole("article", { name: "us-1" })
          .getByText("Not answering", { exact: true }),
      ).toBeVisible({ timeout: 30_000 });
      await shot("fleet-one-down");
      // The fleet has said us-1 is not answering, so its tabs say it once more only, quietly.
      const nothing = page.getByText(/^Nothing to show until us-1 answers/);
      await page.goto("/leaders/us-1/jobs");
      await expect(nothing).toBeVisible();
      await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
      await shot("leader-down");
      const down: [name: string, path: string][] = [
        ["leader-down-pools", "/leaders/us-1/pools"],
        ["leader-down-locations", "/leaders/us-1/locations"],
        ["leader-down-consent", "/leaders/us-1/consent"],
      ];
      for (const [name, path] of down) {
        await page.goto(path);
        await expect(nothing).toBeVisible();
        await shot(name);
      }

      // The fleet in its other states. Each answer is the real one, changed on the way.
      await setLeaderMode(request, "us-1", "ok");
      type Leader = { name: string; health: string } & Record<string, unknown>;
      const fleetAs = async (change: (leaders: Leader[]) => Leader[]) => {
        await page.unroute("**/api/fleet");
        await page.route("**/api/fleet", async (route) => {
          const answer = await route.fetch();
          const leaders = (await answer.json()) as Leader[];
          await route.fulfill({ response: answer, json: change(leaders) });
        });
      };
      const copies = (leaders: Leader[], n: number, patch: Partial<Leader> = {}) =>
        Array.from({ length: n }, (_, i) =>
          leaders.map((l) => ({ ...l, ...patch, name: `${l.name}-${i + 1}` })),
        ).flat();

      await fleetAs(() => []);
      await page.goto("/");
      await expect(page.getByText(/You have no role/)).toBeVisible();
      await shot("fleet-empty");

      await fleetAs((leaders) => copies(leaders, 6, { health: "credential_revoked" }));
      await page.goto("/");
      await settled(page);
      await shot("fleet-needs-look-many");
      const showAll = page.getByRole("button", { name: /^Show all/ });
      await showAll.click();
      await shot("fleet-needs-look-open");

      await fleetAs((leaders) => copies(leaders, 6));
      await page.goto("/");
      await settled(page);
      await shot("fleet-12-leaders");

      await fleetAs((leaders) => copies(leaders, 20));
      await page.goto("/");
      await settled(page);
      await shot("fleet-40-leaders", false);
      if (width > 900) await shot("rail-40-leaders", false);

      // The first answer still pending: placeholders where the totals and cards will be.
      await page.unroute("**/api/fleet");
      let release: () => void = () => undefined;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route("**/api/fleet", async (route) => {
        await held;
        await route.continue();
      });
      await page.goto("/", { waitUntil: "commit" });
      await expect(page.getByText(/^Loading the fleet/)).toBeVisible();
      await shot("fleet-loading", false);
      release();
      await settled(page);

      // A refresh that fails after a good answer: the cards stay and a notice says so.
      await page.unroute("**/api/fleet");
      await page.goto("/");
      await settled(page);
      await page.route("**/api/fleet", (route) => route.fulfill({ status: 503, json: { detail: "unavailable" } }));
      await expect(page.getByRole("alert").filter({ hasText: "The last check did not work" })).toBeVisible({
        timeout: 30_000,
      });
      await shot("fleet-failed-refresh");

      // The states a leader can be in, each produced the way the console produces it, so the
      // page under the header is the real one (a leader invented in the fleet answer alone is
      // unknown to the rest of the console, and its page showed "You cannot see this leader"
      // under every state). us-1 is switched off under Administration; then it is switched on
      // again and revokes the console's credential; off-1 is added switched off; lost-1 and
      // new-1 are added at addresses nothing answers. Last in the test: nothing is put back.
      await page.unroute("**/api/fleet");
      await page.context().clearCookies();
      await signIn(page, "admin");
      const session = (await (await page.request.get("/api/session")).json()) as { csrf_token: string };
      const administer = async (method: "post" | "patch", path: string, data: unknown) => {
        const answer = await page.request[method](path, {
          data,
          headers: { "X-CSRF-Token": session.csrf_token, Origin: ORIGIN },
        });
        expect(answer.ok(), `${method} ${path} answered ${answer.status()}`).toBe(true);
      };
      const pill = (text: string | RegExp) =>
        page.getByRole("main").locator(".status-pill").filter({ hasText: text });
      const card = (name: string, text: string) =>
        page.getByRole("article", { name }).getByText(text, { exact: true });

      await administer("patch", "/api/admin/leaders/us-1", { enabled: false });
      for (const tab of ["pools", "jobs"]) {
        await page.goto(`/leaders/us-1/${tab}`);
        await expect(pill("Switched off")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText(/^Nothing to show while us-1 is switched off/)).toBeVisible();
        await settled(page);
        await shot(tab === "pools" ? "leader-state-off" : "leader-state-off-jobs");
      }

      await administer("patch", "/api/admin/leaders/us-1", { enabled: true });
      await setLeaderMode(request, "us-1", "revoked");
      for (const [name, enabled] of [["off-1", false], ["lost-1", true], ["new-1", true]] as const) {
        await administer("post", "/api/admin/leaders", {
          name,
          base_url: `https://${name}.leaders.example`,
          labels: { region: "eu" },
          credential: CREDENTIAL,
          enabled,
        });
      }
      // A leader waits for its first answer for a few seconds only, too briefly to photograph:
      // new-1 is a real leader, and its health alone is held at "not answered yet" on the way.
      await fleetAs((leaders) =>
        leaders.map((l) => (l.name === "new-1" ? { ...l, health: "pending", consecutive_failures: 2 } : l)),
      );
      await page.goto("/");
      await expect(card("us-1", "Credential revoked")).toBeVisible({ timeout: 30_000 });
      await expect(card("lost-1", "Not answering")).toBeVisible({ timeout: 30_000 });
      await settled(page);
      await shot("fleet-every-state");
      const states: [state: string, name: string, health: string | RegExp][] = [
        ["revoked", "us-1", "Credential revoked"],
        ["lost", "lost-1", "Not answering"],
        ["new", "new-1", /^Not answering yet/],
      ];
      for (const [state, name, health] of states) {
        await page.goto(`/leaders/${name}/pools`);
        await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
        await expect(pill(health)).toBeVisible();
        await expect(page.getByRole("main").locator(".notice").first()).toBeVisible();
        await settled(page);
        await shot(`leader-state-${state}`);
      }
    });
  }
}
