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

  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  const field = shown.getByRole("textbox", { name: "Join token" });
  await expect(field).toHaveValue(/^sst_/);
  // Focus starts on the token itself, selected: Enter, even twice, dismisses nothing.
  await expect(field).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(shown).toBeVisible();
  expect(posts.count()).toBe(1);

  // Not copied yet, and what the token is good for, before anything is pressed.
  await expect(shown.getByText("Not copied yet.")).toBeVisible();
  await expect(shown.getByRole("term")).toHaveText(["Pool", "Can be used", "Expires"]);
  await expect(shown.getByRole("definition").nth(0)).toHaveText("gpu");
  await expect(shown.getByRole("definition").nth(1)).toHaveText("once");

  const plaintext = await field.inputValue();
  await shown.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(shown.getByText("Copied.")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(plaintext);

  // Copied: one "I have stored it" closes it.
  await shown.getByRole("button", { name: "I have stored it" }).click();
  await expect(shown).toHaveCount(0);
  await expect(page.getByText(/^Join token \w{8} is created\.$/)).toBeVisible();
  await expect(rows).toHaveCount(3);
  await noPlaintext(page, plaintext);
  await expect(page.getByRole("button", { name: "Create join token" })).toBeFocused();
});

test("on a phone the one-time token dialog fits: nothing is cut off and every button is in reach", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  await openCreate(page);
  await page.keyboard.press("Enter");
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
  const frame = await shown.boundingBox();
  if (frame === null) throw new Error("the dialog is not on the page");
  expect(frame.x).toBeGreaterThanOrEqual(0);
  expect(frame.x + frame.width).toBeLessThanOrEqual(390);
  for (const part of [
    shown.getByRole("textbox", { name: "Join token" }),
    shown.getByRole("button", { name: "Copy", exact: true }),
    shown.getByRole("button", { name: "I have stored it" }),
  ]) {
    await part.scrollIntoViewIfNeeded();
    const box = await part.boundingBox();
    if (box === null) throw new Error("a control is not on the page");
    expect(box.x).toBeGreaterThanOrEqual(frame.x);
    expect(box.x + box.width).toBeLessThanOrEqual(frame.x + frame.width);
    await expect(part).toBeInViewport();
  }
  // Nothing inside the dialog scrolls sideways either.
  expect(await shown.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
});

test("a held Enter creates exactly one token", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  const posts = countPosts(page);
  const form = await openCreate(page);
  await expect(form.getByRole("textbox", { name: "Pool" })).toBeFocused();
  for (let i = 0; i < 7; i += 1) await page.keyboard.down("Enter");
  await page.keyboard.up("Enter");
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
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
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
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
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
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
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  await expect(shown).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: () => Promise.reject(new Error("denied")),
    });
  });
  await shown.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(shown.getByText("Copying did not work. Select the token and copy it yourself.")).toBeVisible();
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
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
  await expect(form).toHaveCount(0);
});

test("Escape pressed again and again cannot close the create dialog mid-request", async ({ page }) => {
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
  for (let i = 0; i < 5; i += 1) {
    await page.keyboard.press("Escape");
    // Chromium force-closes a dialog on a later Escape; the dialog must come straight back.
    await expect.poll(() => form.evaluate((el: HTMLDialogElement) => el.open)).toBe(true);
  }
  await expect(form).toBeVisible();

  release();
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
  await expect(form).toHaveCount(0);
});

test("a slow create that fails shows its error and Create works again", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  let fail = true;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(TOKEN_POST, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    if (!fail) return route.continue();
    await gate;
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ code: "leader_unreachable", message: "leader eu-1 cannot be reached" }),
    });
  });
  const form = await openCreate(page);
  await form.getByRole("button", { name: "Create token" }).click();
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("Escape");
  release();
  await expect(form).toBeVisible();
  await expect(form.getByText("eu-1 is not answering right now.")).toBeVisible();
  await expect(form).not.toContainText("cannot be reached");
  await expect(form.getByRole("button", { name: "Create token" })).not.toHaveAttribute("aria-disabled", "true");

  fail = false;
  await form.getByRole("button", { name: "Create token" }).click();
  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
});

