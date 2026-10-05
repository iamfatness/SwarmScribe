import { mkdirSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "../tests/support";

// Not a test of behaviour: it photographs every screen in both themes at desktop and tablet
// width, into packages/console-web/screens/, for a person to LOOK at. jsdom and axe pass
// layouts that are plainly wrong to the eye (a cut-off column, text under a pinned cell, a
// card stretched to nothing), so a redesign is not done until these have been looked at.
// Run with: npm run screens   (the same harness as npm run e2e; build first).

const OUT = join(process.cwd(), "screens");
const WIDTHS = [1280, 900, 768] as const;
const THEMES = ["dark", "light"] as const;

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
      await page.emulateMedia({ colorScheme: theme });
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
      await page.getByRole("button", { name: "Add leader" }).click();
      await shot("dialog-add-leader", false);
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
      await page.goto("/leaders/us-1/jobs");
      await expect(page.getByRole("alert").first()).toBeVisible();
      await shot("leader-down");

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

      await fleetAs((leaders) => [
        ...leaders,
        { ...leaders[0], name: "rev-1", health: "credential_revoked" } as Leader,
        { ...leaders[0], name: "off-1", health: "disabled", enabled: false } as Leader,
        { ...leaders[0], name: "new-1", health: "pending", consecutive_failures: 2, summary: null, snapshot: null } as Leader,
        { ...leaders[0], name: "lost-1", health: "unreachable", consecutive_failures: 5, last_error: "connect_error", last_success_at: null, summary: null, snapshot: null } as Leader,
      ]);
      await page.goto("/");
      await settled(page);
      await shot("fleet-every-state");

      // A leader's own page in each state it can be in (its tab's read fails: the header is
      // what is photographed).
      for (const state of ["rev-1", "off-1", "new-1", "lost-1"]) {
        await page.goto(`/leaders/${state}/pools`);
        await expect(page.getByRole("heading", { level: 1, name: state })).toBeVisible();
        await expect(page.getByRole("main").locator(".notice").first()).toBeVisible();
        await shot(`leader-state-${state}`);
      }

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
    });
  }
}
