import { THEMES, chooseTheme, expect, expectAccessible, setLeaderMode, signIn, test } from "./support";

// Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
  { path: "/", heading: "Fleet" },
  { path: "/leaders/eu-1/pools", heading: "eu-1" },
  { path: "/leaders/eu-1/jobs", heading: "eu-1" },
  { path: "/leaders/eu-1/locations", heading: "eu-1" },
  { path: "/leaders/eu-1/tokens", heading: "eu-1" },
  { path: "/leaders/eu-1/consent", heading: "eu-1" },
  { path: "/admin/leaders", heading: "Administration" },
  { path: "/admin/grants", heading: "Administration" },
  { path: "/admin/admins", heading: "Administration" },
  { path: "/no-such-page", heading: "Page not found" },
];

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    // The system is set the same way, but it is the choice that decides (dark is the default).
    test.use({ colorScheme: theme });
    test.beforeEach(({ page }) => chooseTheme(page, theme));
    // What was scanned really was this theme.
    test.afterEach(({ page }) => expect(page.locator("html")).toHaveAttribute("data-theme", theme));

    test("the sign-in page has no accessibility violations", async ({ page }) => {
      await page.goto("/sign-in?signed_out=1");
      await expect(page.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
      await expectAccessible(page, `sign-in (${theme})`);
      await page.setViewportSize({ width: 768, height: 1024 });
      await expectAccessible(page, `sign-in at tablet width (${theme})`);
    });

    test("every signed-in page has no accessibility violations", async ({ page }) => {
      await signIn(page, "admin");
      for (const { path, heading } of SIGNED_IN_PAGES) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
        await expect(page.getByText(/^Loading/)).toHaveCount(0);
        await expectAccessible(page, `${path} (${theme})`);
      }
    });

    test("the fleet overview has no accessibility violations in any of its states", async ({
      page,
      request,
    }) => {
      await signIn(page, "admin");
      await expect(page.getByRole("article", { name: "eu-1" }).getByRole("img")).toBeVisible();
      await page.getByRole("button", { name: "region = us" }).click();
      await expectAccessible(page, `fleet, filtered (${theme})`);
      await page.getByRole("button", { name: "All leaders" }).click();
      await setLeaderMode(request, "us-1", "down");
      await expect(
        page.getByRole("article", { name: "us-1" }).getByText("Not answering", { exact: true }),
      ).toBeVisible({ timeout: 30_000 });
      await expectAccessible(page, `fleet, one leader not answering (${theme})`);
    });

    test("a viewer's pages, with their switched-off actions, have no accessibility violations", async ({
      page,
    }) => {
      await signIn(page, "viewer", "/leaders/eu-1/jobs");
      await expect(page.getByRole("button", { name: /^Try again: job / }).first()).toBeDisabled();
      await expectAccessible(page, `jobs as a viewer (${theme})`);
      await page.getByRole("button", { name: /^Failed/ }).click();
      await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
      await expectAccessible(page, `jobs as a viewer, filtered (${theme})`);
      await page.goto("/leaders/eu-1/tokens");
      await expect(page.getByText(/Join tokens need the admin role/)).toBeVisible();
      await expectAccessible(page, `join tokens as a viewer (${theme})`);
    });

    test("the fleet overview has no violations with no leaders, with every kind of leader, and with a label nobody has", async ({
      page,
    }) => {
      // The fleet as the browser gets it: every state a card can be in, one with the longest
      // name and label the registry allows, and (below) a fleet request that fails.
      type Leader = Record<string, unknown>;
      await page.route("**/api/fleet", async (route) => {
        const answer = await route.fetch();
        const [eu] = (await answer.json()) as Leader[];
        const quiet = { summary: null, snapshot: null, last_success_at: null };
        const make = (name: string, patch: Leader): Leader => ({ ...eu, name, labels: { env: "test" }, ...patch });
        const json = [
          eu,
          make("rev-1", { health: "credential_revoked" }),
          make("off-1", { health: "disabled", enabled: false }),
          make("new-1", { health: "pending", consecutive_failures: 2, ...quiet }),
          make("fresh-1", { health: "pending", consecutive_failures: 0, ...quiet }),
          make("lost-1", { health: "unreachable", consecutive_failures: 5, last_error: "connect_error", ...quiet }),
          make(`L${"o".repeat(99)}`, { labels: { note: "v".repeat(200) } }),
        ];
        await route.fulfill({ response: answer, json });
      });
      await signIn(page, "admin");
      await expect(page.getByRole("article", { name: "lost-1" })).toBeVisible();
      await expect(page.getByText(/^Loading/)).toHaveCount(0);
      await expectAccessible(page, `fleet, every kind of leader (${theme})`);
      await page.getByRole("button", { name: `note = ${"v".repeat(200)}` }).click();
      await expectAccessible(page, `fleet, filtered to a long label (${theme})`);
      await page.goto("/?label=region%3Dmoon");
      await expect(page.getByText("No leader has the label region=moon.")).toBeVisible();
      await expectAccessible(page, `fleet, a label nobody has (${theme})`);
      await page.unroute("**/api/fleet");

      await page.route("**/api/fleet", (route) => route.fulfill({ json: [] }));
      await page.goto("/");
      await expect(page.getByText(/You have no role on any leader yet/)).toBeVisible();
      await expectAccessible(page, `fleet, no leaders (${theme})`);
    });

    test("the fleet overview has no violations while a refresh is failing", async ({ page }) => {
      await signIn(page, "admin");
      await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
      await page.route("**/api/fleet", (route) =>
        route.fulfill({ status: 503, json: { code: "unavailable", message: "down" } }),
      );
      await expect(page.getByRole("alert")).toContainText("The last check did not work", { timeout: 30_000 });
      await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
      await expectAccessible(page, `fleet, last check failed (${theme})`);
    });

    test("the join token dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/leaders/eu-1/tokens");
      await page.getByRole("button", { name: "Create join token" }).click();
      await expectAccessible(page, `create token dialog (${theme})`);
      await page.getByRole("dialog").getByRole("button", { name: "Create token" }).click();
      const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
      await expect(shown).toBeVisible();
      await expectAccessible(page, `token created dialog (${theme})`);
      await shown.getByRole("button", { name: "I have stored it" }).click();
      await expect(shown.getByText(/The token is not shown again/)).toBeVisible();
      await expectAccessible(page, `token created dialog, asking (${theme})`);
      await shown.getByRole("button", { name: "I have stored it" }).click();
      await expect(shown).toHaveCount(0);

      await page
        .getByRole("button", { name: /^Revoke token / })
        .first()
        .click();
      await expectAccessible(page, `revoke token confirm dialog (${theme})`);
    });

    test("the job and location dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/leaders/eu-1/jobs");
      await page
        .getByRole("button", { name: /^Cancel job / })
        .first()
        .click();
      await expectAccessible(page, `cancel job confirm dialog (${theme})`);
      await page.getByRole("button", { name: "No, go back" }).click();
      await page
        .getByRole("button", { name: /^Priority of job / })
        .first()
        .click();
      await expectAccessible(page, `priority dialog (${theme})`);
      await page.getByRole("button", { name: "Cancel", exact: true }).click();

      await page.goto("/leaders/eu-1/locations");
      await page.getByRole("button", { name: "Add location" }).click();
      await page.getByRole("combobox", { name: "Channels" }).selectOption("stereo_split");
      await expectAccessible(page, `add location dialog (${theme})`);
      // The form's own validation errors, shown beside their fields.
      await page.getByRole("dialog").getByRole("button", { name: "Add location" }).click();
      await expect(page.getByRole("dialog").getByRole("alert").first()).toBeVisible();
      await expectAccessible(page, `add location dialog with errors (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Switch off intake" }).click();
      await expectAccessible(page, `switch off location confirm dialog (${theme})`);
    });

    test("administration's refusal of a viewer has no accessibility violations", async ({ page }) => {
      await signIn(page, "viewer", "/admin/leaders");
      await expect(page.getByText(/Administration is for console administrators/)).toBeVisible();
      await expectAccessible(page, `administration as a viewer (${theme})`);
    });

    test("the administration dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/admin/leaders");
      await page.getByRole("button", { name: "Add a leader" }).click();
      await expectAccessible(page, `add leader dialog (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Edit eu-1" }).click();
      await expectAccessible(page, `edit leader dialog (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Replace credential for eu-1" }).click();
      await expectAccessible(page, `replace credential dialog (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Remove eu-1" }).click();
      await expectAccessible(page, `remove leader confirm dialog (${theme})`);
    });

    test("the top bar and its open menu have no accessibility violations at tablet width", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 768, height: 1024 });
      await signIn(page, "admin");
      await expect(page.getByRole("heading", { level: 1, name: "Fleet" })).toBeVisible();
      await expect(page.getByText(/^Loading/)).toHaveCount(0);
      await expectAccessible(page, `fleet at tablet width (${theme})`);
      await page.getByRole("button", { name: "Menu" }).click();
      await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
      await expectAccessible(page, `fleet at tablet width, menu open (${theme})`);
    });
  });
}
