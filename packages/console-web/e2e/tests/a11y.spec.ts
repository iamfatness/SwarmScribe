import { THEMES, expect, expectAccessible, signIn, test } from "./support";

// Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
// C3b adds the drill-down tabs and the administration pages to SIGNED_IN_PAGES.
const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
  { path: "/", heading: "Fleet" },
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
  });
}
