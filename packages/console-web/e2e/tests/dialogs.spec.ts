import type { Page } from "@playwright/test";
import { expect, signIn, test } from "./support";

/**
 * Focus is inside the dialog, or has left the page for the browser's own controls (Tab at the
 * end of a modal dialog does that: activeElement is then the body). Never on the page behind.
 */
function focusIsInDialogOrBrowser(page: Page): Promise<boolean> {
  return page.evaluate(
    () =>
      document.activeElement === document.body || document.activeElement?.closest("dialog") != null,
  );
}

// What the native <dialog> and the app's focus rules do in a real browser (jsdom cannot show
// it): modality, Escape, focus return, and a late answer never touching another dialog.

test("a dialog makes the page inert: Tab stays inside, scroll is locked, Escape returns focus", async ({
  page,
}) => {
  await signIn(page, "operator", "/leaders/eu-1/jobs");
  const opener = page.getByRole("button", { name: /^Cancel job / }).first();
  const openerName = await opener.getAttribute("aria-label");
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();

  for (let i = 0; i < 8; i += 1) {
    await page.keyboard.press("Tab");
    expect(await focusIsInDialogOrBrowser(page)).toBe(true);
  }
  for (let i = 0; i < 4; i += 1) {
    await page.keyboard.press("Shift+Tab");
    expect(await focusIsInDialogOrBrowser(page)).toBe(true);
  }
  // The background cannot take focus by script either: it is inert.
  await page.evaluate(() => (document.querySelector("main") as HTMLElement | null)?.focus());
  expect(await focusIsInDialogOrBrowser(page)).toBe(true);
  // No page scroll while it is open.
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe("hidden");

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: openerName ?? "" })).toBeFocused();
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).not.toBe("hidden");
});

test("an answer that arrives after Escape closes nothing else", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/jobs");
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/leaders/eu-1/jobs/*/cancel", async (route) => {
    await gate;
    await route.continue();
  });
  const cancels = page.getByRole("button", { name: /^Cancel job / });
  await cancels.nth(0).click();
  const first = page.getByRole("alertdialog");
  const firstTitle = await first.getByRole("heading").textContent();
  await first.getByRole("button", { name: "Cancel job" }).click();
  await expect(first.getByRole("button", { name: "Working…" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(first).toHaveCount(0);

  await cancels.nth(1).click();
  const second = page.getByRole("alertdialog");
  await expect(second).toBeVisible();
  const secondTitle = await second.getByRole("heading").textContent();
  expect(secondTitle).not.toBe(firstTitle);

  release();
  await expect(page.getByText(/^Job \w{8} is cancelled\.$/)).toBeVisible();
  await expect(second).toBeVisible();
  await expect(second.getByRole("heading")).toHaveText(secondTitle ?? "");
});
