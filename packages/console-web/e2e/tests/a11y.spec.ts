import { THEMES, expect, expectAccessible, signIn, test } from "./support";

// Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
  { path: "/", heading: "Fleet" },
  { path: "/leaders/eu-1/pools", heading: "eu-1" },
  { path: "/leaders/eu-1/jobs", heading: "eu-1" },
  { path: "/leaders/eu-1/locations", heading: "eu-1" },
  { path: "/leaders/eu-1/tokens", heading: "eu-1" },
  { path: "/leaders/eu-1/consent", heading: "eu-1" },
  { path: "/admin/leaders", heading: "Leaders" },
  { path: "/admin/grants", heading: "Grants" },
  { path: "/admin/admins", heading: "Console administrators" },
  { path: "/no-such-page", heading: "Page not found" },
];

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("the sign-in page has no accessibility violations", async ({ page }) => {
      await page.goto("/sign-in?signed_out=1");
      await expect(page.getByRole("link", { name: "Sign in with Microsoft Entra ID" })).toBeVisible();
      await expectAccessible(page, `sign-in (${theme})`);
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

    test("the join token dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/leaders/eu-1/tokens");
      await page.getByRole("button", { name: "Create join token" }).click();
      await expectAccessible(page, `create token dialog (${theme})`);
      await page.getByRole("dialog").getByRole("button", { name: "Create token" }).click();
      const shown = page.getByRole("dialog", { name: "Join token created" });
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
      await page.getByRole("button", { name: "Close" }).click();
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
      await page.getByRole("button", { name: "Disable intake" }).click();
      await expectAccessible(page, `disable location confirm dialog (${theme})`);
    });

    test("the administration dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/admin/leaders");
      await page.getByRole("button", { name: "Add leader" }).click();
      await expectAccessible(page, `add leader dialog (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Edit eu-1" }).click();
      await expectAccessible(page, `edit leader dialog (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Rotate credential for eu-1" }).click();
      await expectAccessible(page, `rotate credential dialog (${theme})`);
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
