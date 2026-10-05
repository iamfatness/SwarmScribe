import type { Page } from "@playwright/test";
import { expect, signIn, test } from "./support";

// The two colour sets, as the browser paints them (src/styles/tokens.css).
const INK = "rgb(20, 17, 13)";
const PAPER = "rgb(243, 234, 217)";

function ground(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
}

/** Opens a confirmation and reports its background: a sheet is the opposite of the page. */
async function sheet(page: Page): Promise<string> {
  await page
    .getByRole("button", { name: /^Cancel job / })
    .first()
    .click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  const colour = await dialog.evaluate((el) => getComputedStyle(el).backgroundColor);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  return colour;
}

test.describe("on a system set to light", () => {
  test.use({ colorScheme: "light" });

  test("the console is light until Dark is chosen, and the choice survives a reload", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await page.reload();
    await expect(page.getByRole("combobox", { name: "Theme" })).toHaveValue("dark");
    expect(await ground(page)).toBe(INK);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("system");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
    expect(await ground(page)).toBe(PAPER);
  });
});

test.describe("on a system set to dark", () => {
  test.use({ colorScheme: "dark" });

  test("the console is dark until Light is chosen", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("light");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);
    // The rail is ink in both themes.
    const rail = await page.getByRole("banner").evaluate((el) => getComputedStyle(el).color);
    expect(rail).toBe(PAPER);
  });
});

