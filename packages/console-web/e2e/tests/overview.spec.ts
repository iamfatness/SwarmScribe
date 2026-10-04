import { expect, setLeaderMode, signIn, test } from "./support";

test("the overview shows each leader's figures and 24-hour chart", async ({ page }) => {
  await signIn(page, "viewer");
  const eu = page.getByRole("row", { name: /eu-1/ });
  await expect(eu.getByText("Reachable", { exact: true })).toBeVisible();
  // The fake eu-1: two queued jobs, 7 completed in the hour, 30 in the day, one failure.
  await expect(eu.getByRole("cell").nth(1)).toHaveText("2");
  await expect(eu.getByRole("cell").nth(2)).toHaveText("7");
  await expect(eu.getByRole("cell").nth(3)).toHaveText("30");
  await expect(eu.getByRole("cell").nth(4)).toHaveText("1");
  await expect(eu.getByRole("cell").nth(5)).toHaveText("default 1 · gpu 1");
  await expect(eu.getByRole("cell").nth(6)).toHaveText(/^1\d min$/);
  await expect(eu.getByText("archive: the root folder is not readable")).toBeVisible();
  await expect(eu.getByRole("img")).toHaveAccessibleName(/Jobs completed per hour over the last 24 hours/);
  const us = page.getByRole("row", { name: /us-1/ });
  await expect(us.getByRole("img")).toHaveAccessibleName(/Unreachable in 4 five-minute periods/);
});

test("the label filter narrows the rows", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("combobox", { name: "Label" }).selectOption("region=us");
  await expect(page.getByRole("rowheader", { name: /us-1/ })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toHaveCount(0);
  await expect(page).toHaveURL("/?label=region%3Dus");
});

test("a leader that stops answering is shown unreachable while the other stays reachable", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await setLeaderMode(request, "us-1", "down");
  const us = page.getByRole("row", { name: /us-1/ });
  await expect(us.getByText("Unreachable", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(us.getByText(/Figures as of/)).toBeVisible();
  await expect(page.getByRole("row", { name: /eu-1/ }).getByText("Reachable", { exact: true })).toBeVisible();
});

test("the layout holds at tablet width", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
  await expect(page.getByRole("region", { name: "Leaders" })).toBeVisible();
});

test("the overview works from the keyboard alone", async ({ page }) => {
  await signIn(page, "viewer");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("combobox", { name: "Label" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("region", { name: "Leaders" })).toBeFocused();
  await page.getByRole("combobox", { name: "Theme" }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

const TABLE_PAGES = [
  "/",
  "/leaders/eu-1/jobs",
  "/leaders/eu-1/pools",
  "/leaders/eu-1/locations",
  "/leaders/eu-1/consent",
  "/leaders/eu-1/tokens",
  "/admin/leaders",
  "/admin/grants",
  "/admin/admins",
];

test("no table breaks a word across lines, and the page never scrolls sideways at tablet width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "admin");
  for (const path of TABLE_PAGES) {
    await page.goto(path);
    await expect(page.getByRole("table").first()).toBeVisible();
    const found = await page.evaluate(() => {
      const broken: string[] = [];
      for (const cell of document.querySelectorAll("th, td")) {
        // Columns that opt in to breaking anywhere (.long) are the only ones allowed to.
        if (cell.classList.contains("long") || cell.querySelector(".long")) continue;
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
          const text = node.textContent ?? "";
          for (const match of text.matchAll(/\S+/g)) {
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + match[0].length);
            // A word that wraps in the middle has client rects on more than one line.
            const tops = new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top)));
            if (tops.size > 1) broken.push(`${cell.tagName} "${match[0]}"`);
          }
        }
      }
      for (const head of document.querySelectorAll("thead th")) {
        const wraps = getComputedStyle(head).whiteSpace;
        if (wraps !== "nowrap" && !head.closest(".fleet-table")) broken.push(`header ${head.textContent} is ${wraps}`);
      }
      return broken;
    });
    expect(found, path).toEqual([]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});

