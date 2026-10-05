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

// Dark is the default for everyone: the system's setting decides nothing until a person
// chooses System in the Theme switch.
test.describe("on a system set to light", () => {
  test.use({ colorScheme: "light" });

  test("the console is dark until Light is chosen, and the choice survives a reload", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    const theme = page.getByRole("combobox", { name: "Theme" });
    await expect(theme).toHaveValue("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await theme.selectOption("light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);

    await page.reload();
    await expect(theme).toHaveValue("light");
    expect(await ground(page)).toBe(PAPER);

    // System is a choice like the other two: here it means light, and it is remembered.
    await theme.selectOption("system");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "system");
    expect(await ground(page)).toBe(PAPER);
    await page.reload();
    await expect(theme).toHaveValue("system");
    expect(await ground(page)).toBe(PAPER);

    await theme.selectOption("dark");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);
  });

  test("the sign-in page is dark too, before anyone has signed in or chosen", async ({ page }) => {
    await page.goto("/sign-in?signed_out=1");
    await expect(page.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await ground(page)).toBe(INK);
    expect(await page.evaluate(() => localStorage.getItem("swarmscribe-console-theme"))).toBeNull();
  });
});

test.describe("on a system set to dark", () => {
  test.use({ colorScheme: "dark" });

  test("the console is dark until Light is chosen, and System there is dark", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("light");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);
    // The rail is ink in both themes.
    const rail = await page.getByRole("banner").evaluate((el) => getComputedStyle(el).color);
    expect(rail).toBe(PAPER);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("system");
    expect(await ground(page)).toBe(INK);
  });
});


// The chosen theme is applied by a classic script in <head>, before the first paint, not by
// the app's deferred module script: hold that script back and look at what is painted.
async function firstPaintWhileAppIsHeldBack(
  page: Page,
  chosen: "light" | "dark" | "system" | null,
): Promise<string> {
  await page.addInitScript((choice) => {
    if (choice !== null) localStorage.setItem("swarmscribe-console-theme", choice);
  }, chosen);
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(/\/assets\/index-[0-9a-f]+\.js$/, async (route) => {
    await held;
    await route.continue();
  });
  await page.goto("/sign-in", { waitUntil: "commit" });
  // The stylesheet has applied once the page has a background of its own.
  await page.waitForFunction(() => getComputedStyle(document.documentElement).backgroundColor !== "rgba(0, 0, 0, 0)");
  const painted = await ground(page);
  // Proof the app really had not started: nothing is rendered yet.
  expect(await page.locator("#root").evaluate((el) => el.childElementCount)).toBe(0);
  // No choice stored is dark, whatever the system is set to.
  await expect(page.locator("html")).toHaveAttribute("data-theme", chosen ?? "dark");
  release();
  return painted;
}

test.describe("before the first paint", () => {
  test.describe("system light, nothing chosen", () => {
    test.use({ colorScheme: "light" });
    test("paints ink: dark is the default, and there is no light flash", async ({ page }) => {
      expect(await firstPaintWhileAppIsHeldBack(page, null)).toBe(INK);
    });

    test("paints ink even if the boot script never arrives: the stylesheet itself is dark", async ({
      page,
    }) => {
      await page.route(/\/assets\/theme-[0-9a-f]+\.js$/, (route) => route.abort());
      await page.route(/\/assets\/index-[0-9a-f]+\.js$/, (route) => route.abort());
      await page.goto("/sign-in", { waitUntil: "commit" });
      await page.waitForFunction(
        () => getComputedStyle(document.documentElement).backgroundColor !== "rgba(0, 0, 0, 0)",
      );
      await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
      expect(await ground(page)).toBe(INK);
    });
  });

  test.describe("system light, System chosen", () => {
    test.use({ colorScheme: "light" });
    test("paints paper: System is followed once it is a person's choice", async ({ page }) => {
      expect(await firstPaintWhileAppIsHeldBack(page, "system")).toBe(PAPER);
    });
  });

  test.describe("system light, Dark chosen", () => {
    test.use({ colorScheme: "light" });
    test("paints ink, not paper", async ({ page }) => {
      expect(await firstPaintWhileAppIsHeldBack(page, "dark")).toBe(INK);
    });
  });

  test.describe("system dark, Light chosen", () => {
    test.use({ colorScheme: "dark" });
    test("paints paper, not ink", async ({ page }) => {
      expect(await firstPaintWhileAppIsHeldBack(page, "light")).toBe(PAPER);
    });
  });
});
