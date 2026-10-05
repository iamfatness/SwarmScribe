import { endAllSessions, expect, signIn, test } from "./support";

test("a person without a session is sent to sign in and comes back to the page they asked for", async ({
  page,
}) => {
  await signIn(page, "viewer", "/?label=region%3Deu");
  await expect(page).toHaveURL("/?label=region%3Deu");
  await expect(page.getByRole("heading", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
  await expect(page.getByRole("article", { name: "us-1" })).toHaveCount(0);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("/sign-in?signed_out=1");
  await expect(page.getByText("You are signed out.")).toBeVisible();
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
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});

test("the sign-in page shows the brand beside the card, and stacks them at tablet width", async ({ page }) => {
  await page.goto("/sign-in");
  const card = page.getByRole("main");
  await expect(card.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(card.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
  const pitch = page.getByText("Every leader you look after, in one place.");
  await expect(pitch).toBeVisible();
  await expect(page.getByText("localhost:8900")).toBeVisible();
  const wide = { pitch: await pitch.boundingBox(), card: await card.boundingBox() };
  if (wide.pitch === null || wide.card === null) throw new Error("the sign-in page is not laid out");
  expect(wide.card.x).toBeGreaterThan(wide.pitch.x + wide.pitch.width);

  await page.setViewportSize({ width: 768, height: 1024 });
  const narrow = { pitch: await pitch.boundingBox(), card: await card.boundingBox() };
  if (narrow.pitch === null || narrow.card === null) throw new Error("the sign-in page is not laid out");
  expect(narrow.card.y).toBeGreaterThan(narrow.pitch.y + narrow.pitch.height);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(card.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
  const phoneOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(phoneOverflow).toBeLessThanOrEqual(0);
});
