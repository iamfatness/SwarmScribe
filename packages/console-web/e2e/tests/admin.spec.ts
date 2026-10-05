import { expect, signIn, test } from "./support";

/** The console tests' credential shape (console_testkit.CREDENTIAL): 43 URL-safe characters. */
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);

test("a console administrator adds and removes a grant, and each remove button names its grant", async ({
  page,
}) => {
  await signIn(page, "admin");
  await page.getByRole("link", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Administration" })).toBeFocused();
  const who = page.getByRole("link", { name: "Who can do what" });
  await who.click();
  // A section of the same page: focus stays on its link, as on a leader's tabs.
  await expect(who).toBeFocused();
  await expect(who).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { level: 2, name: "Who can do what" })).toBeVisible();
  const form = page.getByRole("form", { name: "Give a role" });
  await form.getByRole("combobox", { name: "Role" }).selectOption("operator");
  await form.getByRole("textbox", { name: "On which leaders" }).fill("label:region=eu");
  await form.getByRole("combobox", { name: "Who" }).selectOption("domain");
  await form.getByRole("textbox", { name: "Domain" }).fill("example.org");
  await form.getByRole("button", { name: "Give the role" }).click();
  await expect(page.getByText("Everyone at example.org is now operator on label:region=eu.")).toBeVisible();

  // A second grant for the same principal: the two remove buttons still differ by name.
  await form.getByRole("combobox", { name: "Role" }).selectOption("viewer");
  await form.getByRole("textbox", { name: "On which leaders" }).fill("all");
  await form.getByRole("textbox", { name: "Domain" }).fill("example.org");
  await form.getByRole("button", { name: "Give the role" }).click();
  await expect(page.getByText("Everyone at example.org is now viewer on all.")).toBeVisible();
  const removers = page.getByRole("button", { name: /^Remove .* from everyone at example\.org$/ });
  await expect(removers).toHaveCount(2);
  const names = await removers.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
  expect(new Set(names).size).toBe(2);

  const operatorGrant = "Remove operator on label:region=eu from everyone at example.org";
  await page.getByRole("button", { name: operatorGrant }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove the role" }).click();
  await expect(page.getByText("The role is removed.")).toBeVisible();
  await expect(page.getByRole("button", { name: operatorGrant })).toHaveCount(0);
  await expect(removers).toHaveCount(1);
});

test("a console administrator registers, rotates and removes a leader", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  await expect(page.getByRole("heading", { level: 2, name: "2\u00a0leaders" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Adding a leader takes two steps" })).toContainText(
    "swarmscribe-admin console create",
  );
  await page.getByRole("button", { name: "Add a leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("ap-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://ap-1.leaders.example");
  await dialog.getByRole("textbox", { name: "Labels" }).fill("region=ap");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add this leader" }).click();
  await expect(page.getByText("Leader ap-1 is added.")).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "3\u00a0leaders" })).toBeVisible();
  expect(await page.content()).not.toContain(CREDENTIAL);

  await page.getByRole("button", { name: "Replace credential for ap-1" }).click();
  const rotate = page.getByRole("dialog", { name: "Replace the credential for ap-1" });
  await rotate.getByLabel("New console credential").fill(CREDENTIAL);
  await rotate.getByRole("button", { name: "Replace credential" }).click();
  await expect(page.getByText("The credential for ap-1 is replaced.")).toBeVisible();
  expect(await page.content()).not.toContain(CREDENTIAL);

  // The new leader has no fake behind it: it shows in the fleet (after the next 10 s
  // refresh) without figures, and the rest of the page is unaffected.
  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
  await expect(page.getByRole("article", { name: "ap-1" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("article", { name: "ap-1" })).toContainText(
    "Figures appear after the first check that works.",
  );

  await page.getByRole("link", { name: "Administration" }).click();
  await page.getByRole("button", { name: "Remove ap-1" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove leader" }).click();
  await expect(page.getByText("Leader ap-1 is removed.")).toBeVisible();
});

test("the console refuses a leader address it may not call", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add a leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("local-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://localhost");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add this leader" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The console may not call that address.");
});

test("a leader's address is on one line at 1280, 900 and 768, never broken after https://", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  for (const width of [1280, 900, 768]) {
    await page.setViewportSize({ width, height: 900 });
    const region = page.getByRole("region", { name: "Registered leaders" });
    await expect(region.getByRole("row", { name: /^eu-1/ })).toBeVisible();
    const lines = await region.evaluate(async (el) => {
      await document.fonts.ready;
      await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
      return Array.from(el.querySelectorAll("td .path-part"), (part) => {
        const range = document.createRange();
        range.selectNodeContents(part);
        const tops = new Set(Array.from(range.getClientRects(), (rect) => Math.round(rect.top)));
        return `${part.textContent}: ${tops.size}`;
      });
    });
    expect(lines, `at ${width}`).toEqual(["https://eu-1.leaders.example: 1", "https://us-1.leaders.example: 1"]);
    // And the table still fits its region: the room came from the columns beside it.
    expect(await region.evaluate((el) => el.scrollWidth - el.clientWidth), `at ${width}`).toBeLessThanOrEqual(1);
  }
});

test("the last console administrator cannot be removed", async ({ page }) => {
  await signIn(page, "admin", "/admin/admins");
  await page.getByRole("button", { name: /^Remove console administrator / }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "Remove administrator" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The last console administrator cannot be removed.");
  await expect(dialog.getByRole("alert")).toContainText("Add another one first.");
  await expect(dialog.getByRole("alert")).not.toContainText("this is the last");
});

test("a person who is not a console administrator has no administration", async ({ page }) => {
  await signIn(page, "operator");
  await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.goto("/admin/leaders");
  await expect(page.getByText(/Administration is for console administrators/)).toBeVisible();
});
