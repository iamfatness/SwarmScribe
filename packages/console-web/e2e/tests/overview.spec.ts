import type { Locator, Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "./support";

/**
 * How far the page is wider than the window: more than 0 means it scrolls sideways. It is read
 * once the fonts are in and two frames have been drawn, so a page that is still laying out
 * (a long name, a list that has just arrived) is not measured halfway; the limit is the same.
 */
function sidewaysOverflow(page: Page): Promise<number> {
  return page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    return document.documentElement.scrollWidth - window.innerWidth;
  });
}

/**
 * Words that wrap in the middle, in every table cell and every card on the page. Text that
 * opts in to breaking anywhere (.long: a path, an address, an id, a leader's own error text)
 * is the only text allowed to.
 */
function brokenWords(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const broken: string[] = [];
    const roots = document.querySelectorAll("th, td, .leader-card, .stat-tile, .needs-look, .rail, .page-head");
    for (const root of roots) {
      const cell = root.matches("th, td");
      if (cell && (root.classList.contains("long") || root.querySelector(".long"))) continue;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
        if (node.parentElement?.closest(".long, .visually-hidden")) continue;
        const text = node.textContent ?? "";
        for (const match of text.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          // A word that wraps in the middle has client rects on more than one line.
          const tops = new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top)));
          if (tops.size > 1) broken.push(`${root.tagName} "${match[0]}"`);
        }
      }
    }
    for (const head of document.querySelectorAll("thead th")) {
      const wraps = getComputedStyle(head).whiteSpace;
      if (wraps !== "nowrap") broken.push(`header ${head.textContent} is ${wraps}`);
    }
    return broken;
  });
}

test("the overview shows each leader's figures and 24-hour chart", async ({ page }) => {
  await signIn(page, "viewer");
  const eu = page.getByRole("article", { name: "eu-1" });
  await expect(eu.getByText("Answering", { exact: true })).toBeVisible();
  // The fake eu-1: two waiting jobs, 7 finished in the hour, 30 in the day, one failure.
  await expect(eu.getByRole("term")).toHaveText([
    "Waiting",
    "Last hour, finished",
    "Last day, finished",
    "Failed tries, last day",
  ]);
  await expect(eu.getByRole("definition")).toHaveText(["2", "7", "30", "1"]);
  await expect(eu.getByText(/oldest waiting/)).toHaveText(
    /^2\sfollowers · default 1, gpu 1 · oldest waiting 1\d\smin$/,
  );
  await expect(eu.getByRole("list", { name: "Labels" }).getByRole("listitem")).toHaveText([
    "env=prod",
    "region=eu",
  ]);
  await expect(eu.getByRole("img")).toHaveAccessibleName(
    /^eu-1: Finished per hour over the last 24 hours/,
  );
  const us = page.getByRole("article", { name: "us-1" });
  await expect(us.getByRole("img")).toHaveAccessibleName(/No answer in 4 five-minute periods/);
});

test("the overview adds up the fleet and lists what needs a look", async ({ page }) => {
  await signIn(page, "viewer");
  const totals = page.getByRole("region", { name: "Totals" });
  await expect(totals.getByRole("term")).toHaveText([
    "Waiting now",
    "Finished, last hour",
    "Finished, last day",
    "Followers at work",
  ]);
  await expect(totals.getByRole("definition")).toHaveText(["4", "10", "42", "4"]);
  const look = page.getByRole("region", { name: "Needs a look" });
  await expect(look.getByRole("heading", { level: 2, name: "Needs a look" })).toBeVisible();
  // Most serious first: failed tries, then the scan error.
  await expect(look.getByRole("listitem")).toHaveText([
    "2 tries failed in the last day. 1 on eu-1, 1 on us-1.",
    "eu-1 could not scan archive. the root folder is not readable",
  ]);
  await look.getByRole("link", { name: "eu-1 could not scan archive." }).click();
  await expect(page).toHaveURL("/leaders/eu-1/locations");
});

test("the label filter narrows the cards", async ({ page }) => {
  await signIn(page, "viewer");
  const pills = page.getByRole("group", { name: "Show leaders with the label" });
  await expect(pills.getByRole("button", { name: "All leaders" })).toHaveAttribute("aria-pressed", "true");
  await pills.getByRole("button", { name: "region = us" }).click();
  await expect(page.getByRole("article", { name: "us-1" })).toBeVisible();
  await expect(page.getByRole("article", { name: "eu-1" })).toHaveCount(0);
  await expect(page).toHaveURL("/?label=region%3Dus");
  await expect(pills.getByRole("button", { name: "region = us" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("1 of 2 leaders")).toBeVisible();
});

test("a leader that stops answering is shown not answering while the other keeps answering", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await setLeaderMode(request, "us-1", "down");
  const us = page.getByRole("article", { name: "us-1" });
  await expect(us.getByText("Not answering", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(us.getByText(/^No answer since .+, after \w+\stries\.$/)).toBeVisible();
  await expect(us.getByText(/The last figures are from/)).toBeVisible();
  await expect(us.getByRole("link", { name: "See what us-1 last reported" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "us-1 no answer" }),
  ).toBeVisible();
  await expect(
    page.getByRole("article", { name: "eu-1" }).getByText("Answering", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Totals" })).toContainText(
    "These include the last figures from us-1, which the console cannot check right now.",
  );
});

test("the layout holds at tablet width: the rail is a top bar with a menu", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  const eu = page.getByRole("article", { name: "eu-1" });
  await expect(eu).toBeVisible();
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);

  // Everything under the brand sits behind the Menu button until it is asked for.
  const menu = page.getByRole("button", { name: "Menu" });
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();
  await expect(page.getByRole("navigation", { name: "Console" })).toBeHidden();

  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
  await expect(page.getByRole("combobox", { name: "Theme" })).toBeVisible();
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);

  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();

  // Following a link from the menu closes it.
  await menu.click();
  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "eu-1" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeVisible();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
});

test("at tablet width the cards sit two across and nothing is cut off", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  const look = await page.getByRole("region", { name: "Needs a look" }).boundingBox();
  const eu = await page.getByRole("article", { name: "eu-1" }).boundingBox();
  const us = await page.getByRole("article", { name: "us-1" }).boundingBox();
  if (look === null || eu === null || us === null) throw new Error("a card is not on the page");
  // "Needs a look" is a band across the top, as tall as its items; the cards sit two across below it.
  const main = await page.getByRole("main").boundingBox();
  if (main === null) throw new Error("the page has no main region");
  expect(look.x + look.width).toBeGreaterThan(main.x + main.width - 40);
  expect(eu.y).toBeGreaterThanOrEqual(look.y + look.height);
  expect(Math.round(us.y)).toBe(Math.round(eu.y));
  expect(us.x).toBeGreaterThan(eu.x + eu.width);
  for (const box of [look, eu, us]) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(768);
  }
  // The chart fills its card and keeps its height.
  const chart = await page.getByRole("article", { name: "eu-1" }).getByRole("img").boundingBox();
  if (chart === null) throw new Error("the chart is not on the page");
  expect(Math.round(chart.height)).toBe(64);
  expect(chart.width).toBeGreaterThan(eu.width * 0.75);
  expect(await brokenWords(page)).toEqual([]);
});

test("at phone width everything stacks and the page still does not scroll sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "viewer");
  const eu = await page.getByRole("article", { name: "eu-1" }).boundingBox();
  const us = await page.getByRole("article", { name: "us-1" }).boundingBox();
  if (eu === null || us === null) throw new Error("a card is not on the page");
  expect(us.y).toBeGreaterThanOrEqual(eu.y + eu.height);
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);
});

test("the overview works from the keyboard alone", async ({ page }) => {
  await signIn(page, "viewer");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  // The skip link lands past the rail: the first stop is the first control of the page.
  await expect(page.getByRole("button", { name: "All leaders" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Space");
  await expect(page).toHaveURL("/?label=env%3Dprod");
  await expect(page.getByRole("button", { name: "env = prod" })).toBeFocused();

  // Tab reaches a leader's own link without ever leaving the main region.
  const card = page.getByRole("article", { name: "eu-1" }).getByRole("link", { name: "eu-1", exact: true });
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.activeElement?.closest("main") !== null)).toBe(true);
    if (await card.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(card).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/leaders/eu-1/pools");
  await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeFocused();

  await page.getByRole("combobox", { name: "Theme" }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("the longest leader name and a very long label never push the page sideways", async ({ page }) => {
  // The registry allows a name of 100 characters and a label value of 255, with no spaces.
  const name = `L${"o".repeat(99)}`;
  const label = `note=${"v".repeat(200)}`;
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill(name);
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://long.leaders.example");
  await dialog.getByRole("textbox", { name: "Labels" }).fill(label);
  await dialog.getByLabel("Console credential").fill("c".repeat(20) + "_-" + "D".repeat(21));
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(dialog).toHaveCount(0);

  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    // The new leader has no fake behind it: its card appears at the next 10 s refresh.
    await expect(page.getByRole("article", { name })).toBeVisible({ timeout: 20_000 });
    expect(await sidewaysOverflow(page), `fleet at ${width}`).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: `note = ${"v".repeat(200)}` }).click();
    await expect(page.getByRole("article", { name: "eu-1" })).toHaveCount(0);
    expect(await sidewaysOverflow(page), `fleet, filtered, at ${width}`).toBeLessThanOrEqual(0);
    const menu = page.getByRole("button", { name: "Menu" });
    if (await menu.isVisible()) {
      await menu.click();
      await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
      expect(await sidewaysOverflow(page), `menu at ${width}`).toBeLessThanOrEqual(0);
    }
    await page.goto(`/leaders/${name}/pools`);
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    expect(await sidewaysOverflow(page), `leader page at ${width}`).toBeLessThanOrEqual(0);
  }
});

// Every table with row actions: its page, the name of its scrolling region, and whether it
// has been folded to fit at every width (the leader's tabs and the join tokens have; the
// Administration tables are folded in their own task and until then scroll at tablet width).
const ACTION_TABLES: [path: string, region: string, fits: boolean][] = [
  ["/leaders/eu-1/jobs", "Job list", true],
  ["/leaders/eu-1/pools", "Followers", true],
  ["/leaders/eu-1/locations", "Location list", true],
  ["/leaders/eu-1/tokens", "Join token list", true],
  ["/admin/leaders", "Registered leaders", false],
  ["/admin/grants", "Grants", false],
  ["/admin/admins", "Console administrators", false],
];

// Tables with no row actions, in the same pages or on their own.
const PLAIN_TABLES: [path: string, region: string][] = [
  ["/leaders/eu-1/pools", "Pools"],
  ["/leaders/eu-1/consent", "Consent by location"],
];

/** How far a table's region scrolls sideways: more than 1 means a column does not fit. */
async function settledOverflow(region: Locator): Promise<number> {
  // Layout can still be moving just after a page opens (fonts, the first list arriving), so
  // the width is read twice, a frame apart, until it stops changing. The limit is not eased.
  let last = Number.NaN;
  for (let tries = 0; tries < 20; tries += 1) {
    const now = await region.evaluate(
      (el) =>
        new Promise<number>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve(el.scrollWidth - el.clientWidth)));
        }),
    );
    if (now === last) return now;
    last = now;
  }
  return last;
}

// A pinned Actions column would hide the text beneath it at rest, so a table either fits its
// region (all of the leader's own tables, at every width) or, where it is still too wide
// (Administration, join tokens, at tablet width), scrolls with nothing pinned over its text
// and Actions reachable by scrolling.
test("every table fits at 1280 and 900, and the leader's own tables at 768 too", async ({ page }) => {
  await signIn(page, "admin");
  for (const width of [1280, 900, 768]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const [path, name, fits] of ACTION_TABLES) {
      await page.goto(path);
      const region = page.getByRole("region", { name, exact: true });
      await expect(region).toBeVisible();
      await expect(page.getByText(/^Loading/)).toHaveCount(0);
      const where = `${path} at ${width}`;
      const widest = await settledOverflow(region);
      if (width >= 900 || fits) expect(widest, `${where} needs no sideways scroll`).toBeLessThanOrEqual(1);

      // No cell is pinned over its neighbours, so no column's text is hidden under another.
      const pinned = await region.evaluate(
        (el) => Array.from(el.querySelectorAll("td, th")).filter((c) => getComputedStyle(c).position === "sticky").length,
      );
      expect(pinned, `${where}: nothing is pinned`).toBe(0);

      // Scrolled all the way right, the Actions header and the first row's buttons are in view.
      await region.evaluate((el) => {
        el.scrollLeft = el.scrollWidth;
      });
      const frame = await region.boundingBox();
      const button = region.locator("td.actions button").first();
      const header = region.getByRole("columnheader", { name: "Actions" });
      for (const target of [button, header]) {
        const box = await target.boundingBox();
        if (frame === null || box === null) throw new Error(`${where}: nothing to measure`);
        expect(box.x, where).toBeGreaterThanOrEqual(frame.x);
        expect(box.x + box.width, where).toBeLessThanOrEqual(frame.x + frame.width + 1);
      }
      // And the point at the middle of that button is the button itself: nothing over it.
      const onTop = await button.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return hit !== null && el.contains(hit);
      });
      expect(onTop, where).toBe(true);
    }
    for (const [path, name] of PLAIN_TABLES) {
      await page.goto(path);
      const region = page.getByRole("region", { name, exact: true });
      await expect(region).toBeVisible();
      expect(await settledOverflow(region), `${path} ${name} at ${width} needs no sideways scroll`).toBeLessThanOrEqual(1);
    }
  }
});

/** Names in a path that wrap across lines although the whole name would fit in its table cell. */
function splitPieces(page: Page, region: string): Promise<string[]> {
  return page.evaluate((label) => {
    const root = document.querySelector(`[role='region'][aria-label='${label}']`);
    const split: string[] = [];
    if (root === null) return ["no such region"];
    for (const mono of root.querySelectorAll("td .mono")) {
      const walker = document.createTreeWalker(mono, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
        const text = node.textContent ?? "";
        for (const piece of text.matchAll(/[^/\\]+/g)) {
          const range = document.createRange();
          range.setStart(node, piece.index);
          range.setEnd(node, piece.index + piece[0].length);
          const tops = new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top)));
          if (tops.size < 2) continue;
          // A name wider than its whole cell has to break somewhere; any other one must not.
          const cell = mono.closest("td");
          const style = getComputedStyle(mono);
          const context = document.createElement("canvas").getContext("2d");
          if (cell === null || context === null) continue;
          context.font = style.font;
          const room = cell.clientWidth - parseFloat(getComputedStyle(cell).paddingLeft) - parseFloat(getComputedStyle(cell).paddingRight);
          if (context.measureText(piece[0]).width <= room) split.push(piece[0]);
        }
      }
    }
    return split;
  }, region);
}

test("a long recording path breaks after a slash, not in the middle of a folder or file name", async ({
  page,
}) => {
  const key = "incoming/2026-10/east-hall-recordings/morning-service/meeting-2026-10-04-final.wav";
  await page.route("**/api/leaders/eu-1/jobs**", async (route) => {
    const answer = await route.fetch();
    const jobs = (await answer.json()) as Record<string, unknown>[];
    await route.fulfill({ response: answer, json: jobs.map((job) => ({ ...job, key })) });
  });
  await signIn(page, "admin");
  for (const width of [1280, 900, 768]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/leaders/eu-1/jobs");
    const region = page.getByRole("region", { name: "Job list", exact: true });
    await expect(region.getByText(key).first()).toBeVisible();
    expect(await settledOverflow(region), `jobs with a long path at ${width}`).toBeLessThanOrEqual(1);
    expect(await splitPieces(page, "Job list"), `pieces split at ${width}`).toEqual([]);
    expect(await sidewaysOverflow(page), `page at ${width}`).toBeLessThanOrEqual(0);
  }
});

const TABLE_PAGES = [
  "/leaders/eu-1/jobs",
  "/leaders/eu-1/pools",
  "/leaders/eu-1/locations",
  "/leaders/eu-1/consent",
  "/leaders/eu-1/tokens",
  "/admin/leaders",
  "/admin/grants",
  "/admin/admins",
];

test("no table or card breaks a word across lines, and the page never scrolls sideways at tablet width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "admin");
  await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
  expect(await brokenWords(page), "/").toEqual([]);
  expect(await sidewaysOverflow(page), "/").toBeLessThanOrEqual(0);
  for (const path of TABLE_PAGES) {
    await page.goto(path);
    await expect(page.getByRole("table").first()).toBeVisible();
    expect(await brokenWords(page), path).toEqual([]);
    expect(await sidewaysOverflow(page), path).toBeLessThanOrEqual(0);
  }
});
