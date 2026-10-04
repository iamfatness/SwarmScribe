import type { Page } from "@playwright/test";
import { expect, signIn, test } from "./support";

const TOKEN_POST = "**/api/leaders/eu-1/tokens";

async function openCreate(page: Page) {
  await page.getByRole("button", { name: "Create join token" }).click();
  const form = page.getByRole("dialog", { name: "Create a join token for eu-1" });
  await expect(form).toBeVisible();
  return form;
}

function countPosts(page: Page): { count: () => number } {
  let n = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/leaders/eu-1/tokens")) n += 1;
  });
  return { count: () => n };
}

async function noPlaintext(page: Page, plaintext: string): Promise<void> {
  expect(await page.content()).not.toContain(plaintext);
  expect(page.url()).not.toContain(plaintext);
  expect(await page.title()).not.toContain(plaintext);
  const stored = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }]));
  expect(stored).not.toContain(plaintext);
}

test("a join token's plaintext is shown once, copied, and gone when the dialog closes", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  const rows = page.getByRole("region", { name: "Join token list" }).getByRole("row");
  await expect(rows).toHaveCount(2);
  const posts = countPosts(page);

  await page.getByRole("button", { name: "Create join token" }).focus();
  await page.keyboard.press("Enter");
  const form = page.getByRole("dialog", { name: "Create a join token for eu-1" });
  await expect(form.getByRole("textbox", { name: "Pool" })).toBeFocused();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("gpu");
  await page.keyboard.press("Enter");

  const shown = page.getByRole("dialog", { name: "Join token created" });
  const field = shown.getByRole("textbox", { name: "Join token" });
  await expect(field).toHaveValue(/^sst_/);
  // Focus starts on the token itself, selected: Enter, even twice, dismisses nothing.
  await expect(field).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(shown).toBeVisible();
  expect(posts.count()).toBe(1);

  const plaintext = await field.inputValue();
  await shown.getByRole("button", { name: "Copy token" }).click();
  await expect(shown.getByText("Copied to the clipboard.")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(plaintext);

  // Copied: one "I have stored it" closes it.
  await shown.getByRole("button", { name: "I have stored it" }).click();
  await expect(shown).toHaveCount(0);
  await expect(page.getByText(/^Join token \w{8} is created\.$/)).toBeVisible();
  await expect(rows).toHaveCount(3);
  await noPlaintext(page, plaintext);
  await expect(page.getByRole("button", { name: "Create join token" })).toBeFocused();
});

test("a held Enter creates exactly one token", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  const posts = countPosts(page);
  const form = await openCreate(page);
  await expect(form.getByRole("textbox", { name: "Pool" })).toBeFocused();
  for (let i = 0; i < 7; i += 1) await page.keyboard.down("Enter");
  await page.keyboard.up("Enter");
  const shown = page.getByRole("dialog", { name: "Join token created" });
  await expect(shown).toBeVisible();
  // More repeats after the one-time dialog is up: it must stay.
  await page.keyboard.down("Enter");
  await page.keyboard.down("Enter");
  await page.keyboard.up("Enter");
  await expect(shown).toBeVisible();
  expect(posts.count()).toBe(1);
  await shown.getByRole("button", { name: "I have stored it" }).click();
  await shown.getByRole("button", { name: "I have stored it" }).click();
  await expect(shown).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Join token list" }).getByRole("row")).toHaveCount(3);
});

test("Escape before the token is copied asks first, and the second Escape closes", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  await openCreate(page);
  await page.keyboard.press("Enter");
  const shown = page.getByRole("dialog", { name: "Join token created" });
  const plaintext = await shown.getByRole("textbox", { name: "Join token" }).inputValue();
  await page.keyboard.press("Escape");
  await expect(shown).toBeVisible();
  await expect(shown.getByText(/The token is not shown again/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(shown).toHaveCount(0);
  await noPlaintext(page, plaintext);
});

test("'I have stored it' asks before the token is copied, and closes on the second go", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  await openCreate(page);
  await page.keyboard.press("Enter");
  const shown = page.getByRole("dialog", { name: "Join token created" });
  await expect(shown).toBeVisible();
  const stored = shown.getByRole("button", { name: "I have stored it" });
  await stored.click();
  await expect(shown).toBeVisible();
  await expect(shown.getByText(/The token is not shown again/)).toBeVisible();
  await stored.click();
  await expect(shown).toHaveCount(0);
});

test("Escape after a failed copy still asks", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  await openCreate(page);
  await page.keyboard.press("Enter");
  const shown = page.getByRole("dialog", { name: "Join token created" });
  await expect(shown).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: () => Promise.reject(new Error("denied")),
    });
  });
  await shown.getByRole("button", { name: "Copy token" }).click();
  await expect(shown.getByText("Copying failed: select the token and copy it.")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(shown).toBeVisible();
  await expect(shown.getByText(/The token is not shown again/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(shown).toHaveCount(0);
});

test("the create dialog cannot be dismissed while its request is in flight, and the token is not lost", async ({
  page,
}) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(TOKEN_POST, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await gate;
    return route.continue();
  });
  const form = await openCreate(page);
  await form.getByRole("button", { name: "Create token" }).click();
  await expect(form.getByRole("button", { name: "Create token" })).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");
  await expect(form).toBeVisible();
  // aria-disabled: the button is focusable and clickable, and the click must do nothing.
  await form.getByRole("button", { name: "Cancel" }).click({ force: true });
  await expect(form).toBeVisible();

  release();
  const shown = page.getByRole("dialog", { name: "Join token created" });
  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
  await expect(form).toHaveCount(0);
});
