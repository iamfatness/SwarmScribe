import { endAllSessions, expect, signIn, test } from "./support";

test("a person without a session is sent to sign in and comes back to the page they asked for", async ({
  page,
}) => {
  await signIn(page, "viewer", "/?label=region%3Deu");
  await expect(page).toHaveURL("/?label=region%3Deu");
  await expect(page.getByRole("heading", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /us-1/ })).toHaveCount(0);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("/sign-in?signed_out=1");
  await expect(page.getByText("You have signed out.")).toBeVisible();
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in/);
});

test("a session that ends on the server sends the person to sign in at the next refresh", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await endAllSessions(request);
  await expect(page).toHaveURL(/\/sign-in/, { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Sign in to the SwarmScribe console" })).toBeVisible();
});
