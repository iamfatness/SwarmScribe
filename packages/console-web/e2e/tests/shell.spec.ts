import type { Page } from "@playwright/test";
import { chooseTheme, expect, expectAccessible, setLeaderMode, signIn, test } from "./support";

const NARROW = { width: 768, height: 1024 };

function nav(page: Page) {
  return page.getByRole("navigation", { name: "Console" });
}

/** The fleet as the browser gets it, with each leader repeated so the page is tall. */
async function manyLeaders(page: Page, copies: number): Promise<void> {
  await page.route("**/api/fleet", async (route) => {
    const answer = await route.fetch();
    const leaders = (await answer.json()) as { name: string }[];
    const many = Array.from({ length: copies }, (_, i) => leaders.map((l) => ({ ...l, name: `${l.name}-${i}` }))).flat();
    await route.fulfill({ response: answer, json: many });
  });
}

test.describe("the menu at narrow width", () => {
  test.use({ viewport: NARROW });

  test("opens into focus, follows a link, and closes on Escape with focus back on the button", async ({
    page,
  }) => {
    await signIn(page, "operator");
    const menu = page.getByRole("button", { name: "Menu" });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(nav(page)).toBeHidden();

    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await expect(nav(page).getByRole("link", { name: "Fleet" })).toBeFocused();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();

    // Following a link closes it, and focus lands on the new page's heading.
    await nav(page).getByRole("link", { name: "eu-1" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeFocused();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(nav(page)).toBeHidden();

    await menu.click();
    await expect(nav(page).getByRole("link", { name: "Fleet" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(menu).toBeFocused();
  });

  test("closes on a click outside it, and the page behind does not scroll while it is open", async ({
    page,
  }) => {
    await signIn(page, "operator");
    const menu = page.getByRole("button", { name: "Menu" });
    await menu.click();
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe("hidden");
    await page.mouse.click(NARROW.width - 20, NARROW.height - 20);
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    // Focus was inside the menu: it returns to the button, not to the top of the page.
    await expect(menu).toBeFocused();
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe("visible");

    // A press inside the menu that is not on a link leaves it open.
    await menu.click();
    await page.getByText("operator@example.org").click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
  });

  test("a closed menu holds nothing the keyboard can reach", async ({ page }) => {
    await signIn(page, "admin");
    await page.getByRole("button", { name: "Menu" }).focus();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() => document.activeElement?.closest(".rail-panel") != null);
      expect(inside).toBe(false);
    }
  });

  test("a tall menu scrolls inside the bar on a short window", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 360 });
    await manyLeaders(page, 6);
    await signIn(page, "admin");
    await page.getByRole("button", { name: "Menu" }).click();
    const signOut = page.getByRole("button", { name: "Sign out" });
    await signOut.scrollIntoViewIfNeeded();
    await expect(signOut).toBeInViewport();
    // The bar itself never grew past the window.
    const bar = await page.getByRole("banner").boundingBox();
    expect(bar?.height).toBeLessThanOrEqual(360);
  });
});

test("the current page is marked in the rail on every kind of page", async ({ page }) => {
  await signIn(page, "admin");
  await expect(nav(page).getByRole("link", { name: "Fleet" })).toHaveAttribute("aria-current", "page");
  await expect(nav(page).locator("a[aria-current]")).toHaveCount(1);

  await nav(page).getByRole("link", { name: "eu-1" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeFocused();
  await expect(nav(page).getByRole("link", { name: "eu-1" })).toHaveAttribute("aria-current", "page");
  await expect(nav(page).locator("a[aria-current]")).toHaveCount(1);
  // A tab change: still that leader, and focus stays on the tab link.
  const tab = page.getByRole("link", { name: "Jobs" });
  await tab.click();
  await expect(tab).toBeFocused();
  await expect(nav(page).getByRole("link", { name: "eu-1" })).toHaveAttribute("aria-current", "page");

  await nav(page).getByRole("link", { name: "Administration" }).click();
  await expect(nav(page).getByRole("link", { name: "Administration" })).toHaveAttribute("aria-current", "page");
  await page.getByRole("link", { name: "Who can do what" }).click();
  await expect(nav(page).getByRole("link", { name: "Administration" })).toHaveAttribute("aria-current", "page");
  await expect(nav(page).locator("a[aria-current]")).toHaveCount(1);
});

test("a leader that does not answer says so in words in the rail", async ({ page, request }) => {
  await signIn(page, "viewer");
  await setLeaderMode(request, "us-1", "down");
  await expect(nav(page).getByRole("link", { name: /^us-1/ })).toContainText("no answer", {
    timeout: 30_000,
  });
});

test("the skip link is first and lands on the main region", async ({ page }) => {
  await signIn(page, "viewer");
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Fleet" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main#main")).toBeFocused();
});

test.describe("the rail on a tall page", () => {
  test.use({ viewport: { width: 1280, height: 600 } });

  test("runs the height of the window and stays put as the page scrolls", async ({ page }) => {
    await manyLeaders(page, 8);
    await signIn(page, "admin");
    await expect(page.getByRole("heading", { level: 1, name: "Fleet" })).toBeVisible();
    const rail = page.getByRole("banner");
    const before = await rail.boundingBox();
    expect(Math.abs(before?.y ?? 1)).toBeLessThan(1);
    expect(Math.round(before?.height ?? 0)).toBe(600);
    await page.mouse.move(700, 300);
    await page.mouse.wheel(0, 1500);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(500);
    const after = await rail.boundingBox();
    expect(Math.abs(after?.y ?? 1)).toBeLessThan(1);
    expect(Math.round(after?.height ?? 0)).toBe(600);
  });
});

const leaderList = (page: Page) => nav(page).getByRole("list", { name: "Leaders" });

test.describe("Administration in the rail", () => {
  test.use({ viewport: { width: 1280, height: 700 } });

  for (const pairs of [6, 20]) {
    test(`is visible and reachable with ${pairs * 2} leaders`, async ({ page }) => {
      await manyLeaders(page, pairs);
      await signIn(page, "admin");
      const admin = nav(page).getByRole("link", { name: "Administration" });
      await expect(admin).toBeInViewport({ ratio: 1 });
      // Fleet stays above the list, Administration below it, both without scrolling anything.
      await expect(nav(page).getByRole("link", { name: "Fleet", exact: true })).toBeInViewport({ ratio: 1 });
      const list = await leaderList(page).boundingBox();
      const at = await admin.boundingBox();
      if (list === null || at === null) throw new Error("the rail is not laid out");
      expect(at.y).toBeGreaterThanOrEqual(list.y + list.height - 1);
      // It still works: a click goes to Administration.
      await admin.click();
      await expect(page).toHaveURL(/\/admin\/leaders/);
      await expect(admin).toBeInViewport({ ratio: 1 });
    });
  }
});

test.describe("the rail with twenty leaders", () => {
  test.use({ viewport: { width: 1280, height: 700 } });

  test("scrolls only the list of leaders, with Administration, the person, theme and Sign out always in view", async ({
    page,
  }) => {
    await manyLeaders(page, 10);
    await signIn(page, "admin");
    await expect(nav(page).getByRole("link", { name: "eu-1-9" })).toBeAttached();
    await expect(nav(page).getByRole("list", { name: "Leaders" }).getByRole("listitem")).toHaveCount(20);
    const signOut = page.getByRole("button", { name: "Sign out" });
    await expect(signOut).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole("combobox", { name: "Theme" })).toBeInViewport({ ratio: 1 });
    await expect(nav(page).getByRole("link", { name: "Administration" })).toBeInViewport({ ratio: 1 });
    // The list of leaders is what scrolls: it is taller than the room it has, and the rail itself is not.
    const sizes = await page.evaluate(() => {
      const list = document.querySelector<HTMLElement>(".rail-panel nav ul[aria-label='Leaders']");
      const rail = document.querySelector<HTMLElement>(".rail");
      return {
        listScrolls: (list?.scrollHeight ?? 0) > (list?.clientHeight ?? 0),
        railScrolls: (rail?.scrollHeight ?? 0) > (rail?.clientHeight ?? 0),
      };
    });
    expect(sizes).toEqual({ listScrolls: true, railScrolls: false });
    // Scrolling the list to its end leaves Sign out where it was.
    const at = await signOut.boundingBox();
    const admin = nav(page).getByRole("link", { name: "Administration" });
    const adminAt = await admin.boundingBox();
    await leaderList(page).evaluate((el) => el.scrollTo(0, el.scrollHeight));
    expect(await signOut.boundingBox()).toEqual(at);
    expect(await admin.boundingBox()).toEqual(adminAt);
    // And from the keyboard: Tab from the last leader reaches Administration, the theme, then Sign out.
    await nav(page).getByRole("link", { name: "us-1-9" }).focus();
    await page.keyboard.press("Tab");
    await expect(nav(page).getByRole("link", { name: "Administration" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("combobox", { name: "Theme" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(signOut).toBeFocused();
    await expect(signOut).toBeInViewport({ ratio: 1 });
  });
});

test.describe("long names", () => {
  for (const width of [1280, 768, 390]) {
    test(`do not break the rail or push the page sideways at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const long = "leader-".padEnd(100, "x");
      await page.route("**/api/fleet", async (route) => {
        const answer = await route.fetch();
        const leaders = (await answer.json()) as { name: string }[];
        const json = leaders.map((l, i) => ({ ...l, name: i === 0 ? long : l.name }));
        await route.fulfill({ response: answer, json });
      });
      await page.route("**/api/session", async (route) => {
        const answer = await route.fetch();
        const session = (await answer.json()) as Record<string, unknown>;
        const email = `${"a".repeat(80)}@${"b".repeat(40)}.example.org`;
        await route.fulfill({ response: answer, json: { ...session, email } });
      });
      await signIn(page, "viewer");
      if (width <= 900) await page.getByRole("button", { name: "Menu" }).click();
      await expect(nav(page).getByRole("link", { name: long })).toBeVisible();
      const overflow = await page.evaluate(() => {
        const rail = document.querySelector<HTMLElement>(".rail");
        return {
          page: document.documentElement.scrollWidth - window.innerWidth,
          rail: rail ? rail.scrollWidth - rail.clientWidth : 0,
        };
      });
      expect(overflow.page).toBeLessThanOrEqual(0);
      expect(overflow.rail).toBeLessThanOrEqual(0);
    });
  }
});

test.describe("accessibility of the open menu at phone width", () => {
  for (const theme of ["light", "dark"] as const) {
    test.describe(theme, () => {
      test.use({ colorScheme: theme, viewport: { width: 390, height: 844 } });
      test.beforeEach(({ page }) => chooseTheme(page, theme));

      test(`has no violations, closed and open (${theme})`, async ({ page }) => {
        await signIn(page, "admin");
        await expect(page.getByText(/^Loading/)).toHaveCount(0);
        await expectAccessible(page, `fleet at phone width (${theme})`);
        await page.getByRole("button", { name: "Menu" }).click();
        await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
        await expectAccessible(page, `phone menu open (${theme})`);
      });
    });
  }
});
