import type { Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "./support";

/** Presses Tab until the focused element's accessible name matches, as a keyboard user would. */
async function tabTo(page: Page, name: RegExp, limit = 60): Promise<void> {
  for (let i = 0; i < limit; i += 1) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el?.getAttribute("aria-label") ?? el?.textContent ?? "";
    });
    if (name.test(focused.trim())) return;
  }
  throw new Error(`Tab never reached ${name}`);
}

test("an operator retries a failed job and cancels a queued one with the keyboard alone", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/jobs");
  const failed = page.getByRole("row").filter({ hasText: "the engine stopped" });
  await expect(failed).toBeVisible();

  await tabTo(page, /^Retry job /);
  await page.keyboard.press("Enter");
  await expect(page.getByText(/^Job \w{8} is queued again\.$/)).toBeVisible();
  // The Retry button went with the row's state; the focus must not fall back to the page top.
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.closest("[role='region']")?.getAttribute("aria-label")))
    .toBe("Job list");

  await tabTo(page, /^Cancel job /);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog", { name: /^Cancel job \w{8}\?$/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Cancel job" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/^Job \w{8} is cancelled\.$/)).toBeVisible();
});

test("a job's priority is set from its dialog by keyboard", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/jobs");
  await tabTo(page, /^Priority of job /);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: /^Priority of job \w{8}$/ });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("42");
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/^Priority of job \w{8} is set\.$/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "42", exact: true })).toBeVisible();
});

test("the job filter narrows by state and survives a reload", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  await page.getByRole("combobox", { name: "State" }).selectOption("failed");
  await expect(page).toHaveURL("/leaders/eu-1/jobs?state=failed");
  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
  await page.reload();
  await expect(page.getByRole("combobox", { name: "State" })).toHaveValue("failed");
});

test("a viewer sees actions disabled with the role they need", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  const retry = page.getByRole("button", { name: /^Retry job / }).first();
  await expect(retry).toBeDisabled();
  await expect(retry).toHaveAccessibleDescription("needs operator");
  await page.getByRole("link", { name: "Join tokens" }).click();
  await expect(page.getByText("Join tokens need the admin role on eu-1. Your role is viewer.")).toBeVisible();
});

test("an operator drains a follower but cannot revoke one", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/pools");
  await expect(
    page.getByRole("region", { name: "Pools" }).getByRole("rowheader", { name: "gpu" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /^Drain follower / })
    .first()
    .click();
  await expect(page.getByText(/^Follower \w{8} is draining\.$/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Revoke follower / }).first()).toBeDisabled();
});

test("an admin revokes a follower and its leased job goes back to the queue", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/pools");
  const row = page
    .getByRole("region", { name: "Followers" })
    .getByRole("row")
    .filter({ hasText: "cuda" })
    .first();
  await row.getByRole("button", { name: /^Revoke follower / }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Revoke follower" }).click();
  await expect(page.getByText(/is revoked; 1 leased jobs went back to the queue\./)).toBeVisible();
});

test("an admin adds, disables, enables and scans locations", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/locations");
  await expect(page.getByText("the root folder is not readable")).toBeVisible();
  await page.getByRole("button", { name: "Add location" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a location to eu-1" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("calls");
  await dialog.getByRole("textbox", { name: "Folder on the leader (absolute path)" }).fill("/srv/calls");
  await dialog.getByRole("combobox", { name: "Channels" }).selectOption("stereo_split");
  await dialog.getByRole("textbox", { name: "Left channel label" }).fill("Agent");
  await dialog.getByRole("textbox", { name: "Right channel label" }).fill("Caller");
  await dialog.getByRole("button", { name: "Add location" }).click();
  await expect(page.getByText("Location calls is added.")).toBeVisible();
  await expect(page.getByRole("row", { name: /calls/ })).toContainText("stereo_split (Agent, Caller)");

  await page.getByRole("button", { name: "Disable archive" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Disable location" }).click();
  await expect(page.getByText("archive is disabled.")).toBeVisible();
  await page.getByRole("button", { name: "Enable archive" }).click();
  await expect(page.getByText("archive is enabled.")).toBeVisible();
  await page.getByRole("button", { name: "Scan now intake" }).click();
  await expect(page.getByText("A scan of intake is requested.")).toBeVisible();
  await expect(page.getByRole("row", { name: /intake/ })).toContainText("Scan requested");
});

test("a leader's own refusal is shown and focus stays in the dialog: us-1 caps the console at operator", async ({
  page,
}) => {
  await signIn(page, "admin", "/leaders/us-1/locations");
  await page.getByRole("button", { name: "Disable intake" }).click();
  const dialog = page.getByRole("alertdialog");
  const confirm = dialog.getByRole("button", { name: "Disable location" });
  await confirm.click();
  await expect(dialog.getByRole("alert")).toContainText("Your role does not allow this.");
  await expect(dialog.getByRole("alert")).toContainText("this needs the admin role");
  // The dialog stays open on the failure and keeps focus where the person acted.
  await expect(confirm).toBeFocused();
  expect(await page.evaluate(() => document.activeElement?.closest("dialog") !== null)).toBe(true);
});

test("the consent report shows counts and transcripts to review", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/consent");
  await expect(
    page.getByRole("region", { name: "Consent by location" }).getByRole("row", { name: /intake/ }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Transcripts to review" })).toContainText(
    "transcripts/meeting-4.wav.json",
  );
});

test("with one leader down its tabs report it, and the other leader stays operable", async ({
  page,
  request,
}) => {
  await signIn(page, "operator", "/leaders/us-1/jobs");
  await setLeaderMode(request, "us-1", "down");
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByRole("alert")).toContainText("The leader cannot be reached right now.");
  await expect(page.getByRole("link", { name: "Locations" })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fleet" }).click();
  await page.getByRole("link", { name: "eu-1" }).click();
  await page.getByRole("link", { name: "Jobs" }).click();
  await page
    .getByRole("button", { name: /^Cancel job / })
    .first()
    .click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel job" }).click();
  await expect(page.getByText(/^Job \w{8} is cancelled\.$/)).toBeVisible();
});

test("a drill-down address survives a reload and a leader name without a tab redirects", async ({
  page,
}) => {
  await signIn(page, "viewer", "/leaders/eu-1");
  await expect(page).toHaveURL("/leaders/eu-1/pools");
  await page.getByRole("link", { name: "Consent report" }).click();
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Consent report" })).toBeVisible();
});

test("switching tabs keeps focus on the tab link", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/pools");
  const jobs = page.getByRole("navigation", { name: "eu-1 sections" }).getByRole("link", { name: "Jobs" });
  await jobs.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/leaders/eu-1/jobs");
  await expect(jobs).toBeFocused();
});
