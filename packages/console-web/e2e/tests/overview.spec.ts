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
