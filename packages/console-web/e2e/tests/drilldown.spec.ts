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

  await tabTo(page, /^Try again: job /);
  await page.keyboard.press("Enter");
  await expect(page.getByText(/^Job \w{8} is waiting again\.$/)).toBeVisible();
  // The Try again button went with the row's state; the focus must not fall back to the page top.
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.closest("[role='region']")?.getAttribute("aria-label")))
    .toBe("Job list");

  await tabTo(page, /^Cancel job /);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog", { name: /^Cancel job \w{8}\?$/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "No, go back" })).toBeFocused();
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
  const pills = page.getByRole("group", { name: "Show jobs that are" });
  // The fake leader: two waiting, one being worked on, one failed, one finished, one cancelled.
  await expect(pills.getByRole("button")).toHaveText([
    "All 6",
    "Waiting 2",
    "Being worked on 1",
    "Failed 1",
    "Finished 1",
    "Cancelled 1",
  ]);
  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(7);
  await pills.getByRole("button", { name: /^Failed/ }).click();
  await expect(page).toHaveURL("/leaders/eu-1/jobs?state=failed");
  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
  await page.reload();
  await expect(pills.getByRole("button", { name: /^Failed/ })).toHaveAttribute("aria-pressed", "true");
  await expect(pills.getByRole("button", { name: /^All/ })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
});

test("each job says its state in words and where its recording came from", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  const list = page.getByRole("region", { name: "Job list" });
  await expect(list.getByRole("columnheader")).toHaveText([
    "Job",
    "State",
    "Recording",
    "Pool",
    "Priority",
    "Tries",
    "Queued",
    "Actions",
  ]);
  const failed = list.getByRole("row").filter({ hasText: "the engine stopped" });
  await expect(failed.getByRole("cell").nth(0)).toHaveText("Failed");
  await expect(failed.getByRole("cell").nth(1)).toHaveText(
    "incoming/meeting-5.wavFrom intake · the engine stopped: out of memory",
  );
  await expect(failed.getByRole("cell").nth(4)).toHaveText("3 of 3");
  await expect(list.getByRole("cell", { name: /^With follower \w{8}$/ })).toHaveCount(1);
  await expect(list.getByRole("cell", { name: "Waiting", exact: true })).toHaveCount(2);
  await expect(list.getByRole("row").filter({ hasText: "Cancelled by someone@example.org" })).toHaveCount(1);
  await expect(page.getByText(/^Loaded at /)).toBeVisible();
});

test("a viewer's switched-off actions stay readable at tablet width", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  const region = page.getByRole("region", { name: "Job list" });
  const button = region.getByRole("button", { name: /^Cancel job / }).first();
  await expect(button).toBeDisabled();
  const widest = await region.evaluate((el) => el.scrollWidth - el.clientWidth);
  for (const left of [0, widest]) {
    await region.evaluate((el, x) => {
      el.scrollLeft = x;
    }, left);
    const frame = await region.boundingBox();
    const box = await button.boundingBox();
    // The note that says which role is needed sits under its button, inside the pinned cell.
    const note = await region.getByText("needs operator").first().boundingBox();
    if (frame === null || box === null || note === null) throw new Error("nothing to measure");
    for (const part of [box, note]) {
      expect(part.x).toBeGreaterThanOrEqual(frame.x);
      expect(part.x + part.width).toBeLessThanOrEqual(frame.x + frame.width + 1);
    }
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("a viewer sees actions disabled with the role they need", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  const retry = page.getByRole("button", { name: /^Try again: job / }).first();
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
  await expect(page.getByRole("alert")).toContainText("The leader is not answering right now.");
  await expect(page.getByRole("link", { name: "Locations" })).toBeVisible();

  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
  await page.getByRole("main").getByRole("link", { name: "eu-1", exact: true }).click();
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

test("a leader's page says who the person is here and what its labels are", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/pools");
  const main = page.getByRole("main");
  await expect(main.getByText(/^You are an operator here\./)).toHaveText(
    "You are an operator here. What needs an admin is shown, but switched off.",
  );
  await expect(main.getByRole("list", { name: "Labels" }).getByRole("listitem")).toHaveText([
    "env=prod",
    "region=eu",
  ]);
  await expect(main.getByText("Answering", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "eu-1", exact: true }),
  ).toHaveAttribute("aria-current", "page");
});

test("the role line of a viewer, the longest, sits on one line at 1280", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page, "viewer", "/leaders/eu-1/pools");
  const line = page.getByRole("main").getByText(/^You are a viewer here\./);
  await expect(line).toHaveText(
    "You are a viewer here. What needs an operator or an admin is shown, but switched off.",
  );
  const lines = await line.evaluate((el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)));
  expect(lines).toBe(1);
});

test("switching tabs keeps focus on the tab link", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/pools");
  const jobs = page.getByRole("navigation", { name: "eu-1 sections" }).getByRole("link", { name: "Jobs" });
  await jobs.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/leaders/eu-1/jobs");
  await expect(jobs).toBeFocused();
});
