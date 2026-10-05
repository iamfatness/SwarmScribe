import { expect, signIn, test } from "./support";

/** The console tests' credential shape (console_testkit.CREDENTIAL): 43 URL-safe characters. */
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);

test("a console administrator adds and removes a grant, and each remove button names its grant", async ({
  page,
}) => {
  await signIn(page, "admin");
  await page.getByRole("link", { name: "Administration" }).click();
  await page.getByRole("link", { name: "Grants" }).click();
  const form = page.getByRole("form", { name: "Add a grant" });
  await form.getByRole("combobox", { name: "Role" }).selectOption("operator");
  await form.getByRole("textbox", { name: "Scope" }).fill("label:region=eu");
  await form.getByRole("combobox", { name: "Principal kind" }).selectOption("domain");
  await form.getByRole("textbox", { name: "Principal" }).fill("example.org");
  await form.getByRole("button", { name: "Add grant" }).click();
  await expect(
    page.getByText("Grant added: operator on label:region=eu for domain:example.org."),
  ).toBeVisible();

  // A second grant for the same principal: the two remove buttons still differ by name.
  await form.getByRole("combobox", { name: "Role" }).selectOption("viewer");
  await form.getByRole("textbox", { name: "Scope" }).fill("all");
  await form.getByRole("textbox", { name: "Principal" }).fill("example.org");
  await form.getByRole("button", { name: "Add grant" }).click();
  await expect(page.getByText("Grant added: viewer on all for domain:example.org.")).toBeVisible();
  const removers = page.getByRole("button", { name: /^Remove grant: .* for domain:example\.org$/ });
  await expect(removers).toHaveCount(2);
  const names = await removers.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
  expect(new Set(names).size).toBe(2);

  const operatorGrant = "Remove grant: operator on label:region=eu for domain:example.org";
  await page.getByRole("button", { name: operatorGrant }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove grant" }).click();
  await expect(page.getByText("Grant removed.")).toBeVisible();
  await expect(page.getByRole("button", { name: operatorGrant })).toHaveCount(0);
  await expect(removers).toHaveCount(1);
});

test("a console administrator registers, rotates and removes a leader", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("ap-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://ap-1.leaders.example");
  await dialog.getByRole("textbox", { name: "Labels" }).fill("region=ap");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(page.getByText("Leader ap-1 is added.")).toBeVisible();
  expect(await page.content()).not.toContain(CREDENTIAL);

  await page.getByRole("button", { name: "Rotate credential for ap-1" }).click();
  const rotate = page.getByRole("dialog", { name: "Rotate the credential for ap-1" });
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
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("local-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://localhost");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(dialog.getByRole("alert")).toContainText("That leader URL is not allowed.");
});

test("the last console administrator cannot be removed", async ({ page }) => {
  await signIn(page, "admin", "/admin/admins");
  await page.getByRole("button", { name: /^Remove console administrator / }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "Remove administrator" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The last console administrator cannot be removed.");
});

test("a person who is not a console administrator has no administration", async ({ page }) => {
  await signIn(page, "operator");
  await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.goto("/admin/leaders");
  await expect(page.getByText(/Console administration needs a console administrator/)).toBeVisible();
});
