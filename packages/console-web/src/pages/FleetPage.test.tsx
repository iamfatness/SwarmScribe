import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLastInputForTests } from "../app/activity";
import { FleetProvider } from "../app/fleet";
import { RouterProvider } from "../app/router";
import { fail, mockFetch, reply, type FetchMock } from "../test/fetchMock";
import { NOW, STATUS, history, leader } from "../test/fixtures";
import { FleetPage, LABEL_PILL_LIMIT, concerns } from "./FleetPage";

function renderFleet() {
  return render(
    <RouterProvider>
      <FleetProvider>
        <FleetPage />
      </FleetProvider>
    </RouterProvider>,
  );
}

function withHistory(mock: FetchMock, ...names: string[]): FetchMock {
  for (const name of names)
    mock.on(`GET /api/leaders/${name}/history?hours=24`, reply(200, history()));
  return mock;
}

/** A leader's card: the article named by its heading. */
async function findCard(name: string): Promise<HTMLElement> {
  return screen.findByRole("article", { name });
}

function queryCard(name: string): HTMLElement | null {
  return screen.queryByRole("article", { name });
}

/** A definition list as { term: value }, the way a screen reader pairs them. */
function figures(list: HTMLElement): Record<string, string> {
  const pairs: Record<string, string> = {};
  for (const term of within(list).getAllByRole("term")) {
    pairs[term.textContent ?? ""] = term.nextElementSibling?.textContent ?? "";
  }
  return pairs;
}

const EU = leader();
const US = leader({
  name: "us-1",
  labels: { env: "prod", region: "us" },
  health: "unreachable",
  consecutive_failures: 4,
  last_error: "connect_error",
  last_success_at: "2026-10-04T11:50:00Z",
  snapshot: { taken_at: "2026-10-04T11:50:00Z", status: STATUS },
});

describe("FleetPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    setLastInputForTests(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("shows one card per leader with the overview figures", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    expect(within(card).getByText("Answering")).toBeInTheDocument();
    expect(figures(card)).toEqual({
      Waiting: "3",
      "Last hour, finished": "7",
      "Last day, finished": "30",
      "Failed tries, last day": "1",
    });
    // 420 s at the snapshot, taken 10 s before NOW.
    expect(within(card).getByText(/oldest waiting 7 min/)).toHaveTextContent(
      "3 followers · default 2, gpu 1 · oldest waiting 7 min",
    );
    expect(
      within(card).getByRole("list", { name: "Labels" }),
    ).toHaveTextContent("env=prodregion=eu");
    expect(await within(card).findByRole("img")).toHaveAccessibleName(
      /^eu-1: Finished per hour/,
    );
  });

  it("links each card's heading to the leader", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    const heading = within(card).getByRole("heading", {
      level: 2,
      name: "eu-1",
    });
    expect(within(heading).getByRole("link", { name: "eu-1" })).toHaveAttribute(
      "href",
      "/leaders/eu-1/pools",
    );
  });

  it("shows a leader that is not answering with its last figures and keeps the others working", async () => {
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, US])),
      "eu-1",
      "us-1",
    );
    renderFleet();
    const us = await findCard("us-1");
    expect(us).toHaveClass("leader-card-bad");
    expect(within(us).getByText("Not answering")).toBeInTheDocument();
    expect(
      within(us).getByText(/^No answer since .+, after four tries\.$/),
    ).toBeInTheDocument();
    expect(within(us).getByText(/The last figures are from/)).toHaveTextContent(
      /: 3 waiting, 3 followers\. Recordings already claimed keep going; this console just cannot see them\.$/,
    );
    expect(
      within(us).getByText("Last error: It could not be reached."),
    ).toBeInTheDocument();
    expect(
      within(us).getByRole("link", { name: "See what us-1 last reported" }),
    ).toHaveAttribute("href", "/leaders/us-1/pools");
    // No chart and no fresh figures for a leader that is not answering.
    expect(within(us).queryByRole("img")).not.toBeInTheDocument();
    expect(within(us).queryByRole("term")).not.toBeInTheDocument();
    const eu = await findCard("eu-1");
    expect(within(eu).getByText("Answering")).toBeInTheDocument();
  });

  it("shows a revoked leader's card with its snapshot time", async () => {
    const revoked = leader({ name: "rev-1", health: "credential_revoked" });
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [revoked])),
      "rev-1",
    );
    renderFleet();
    const card = await findCard("rev-1");
    expect(within(card).getByText("Credential revoked")).toBeInTheDocument();
    expect(
      within(card).getByText(/The last figures are from/),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(/A console administrator must replace it\.$/),
    ).toBeInTheDocument();
  });

  it("shows a leader that never answered without figures", async () => {
    const pending = leader({
      name: "new-1",
      health: "pending",
      consecutive_failures: 2,
      summary: null,
      snapshot: null,
    });
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [pending])),
      "new-1",
    );
    renderFleet();
    const card = await findCard("new-1");
    expect(
      within(card).getByText("Not answering yet, two tries"),
    ).toBeInTheDocument();
    expect(
      within(card).getByText("No answer yet, after two tries."),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(
        "Figures appear after the first check that works.",
      ),
    ).toBeInTheDocument();
    expect(
      within(card).queryByRole("link", { name: /last reported/ }),
    ).not.toBeInTheDocument();
    // Nothing to add up: the totals show a dash, never a made-up zero.
    expect(figures(screen.getByRole("region", { name: "Totals" }))).toEqual({
      "Waiting now": "–",
      "Finished, last hour": "–",
      "Finished, last day": "–",
      "Followers at work": "–",
    });
  });

  it("shows a switched-off leader without asking for its history", async () => {
    const off = leader({ name: "off-1", health: "disabled", enabled: false });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [off]));
    renderFleet();
    const card = await findCard("off-1");
    expect(within(card).getByText("Switched off")).toBeInTheDocument();
    expect(
      within(card).getByText(
        "This leader is switched off in the console, so nothing is asked of it.",
      ),
    ).toBeInTheDocument();
    expect(
      mock.callsTo("GET /api/leaders/off-1/history?hours=24"),
    ).toHaveLength(0);
  });

  it("says so when a leader has no followers at work and nothing waiting", async () => {
    const idle = leader({
      summary: {
        ...(EU.summary as NonNullable<typeof EU.summary>),
        queued: 0,
        oldest_queued_age_s: null,
        followers_active_by_pool: {},
      },
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [idle])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    expect(within(card).getByText(/nothing waiting/)).toHaveTextContent(
      /^No followers at work · nothing waiting$/,
    );
    expect(
      figures(screen.getByRole("region", { name: "Totals" }))[
        "Followers at work"
      ],
    ).toBe("0");
  });

  it("shows a health it does not know as the leader's own word, without a chart or made-up figures", async () => {
    const odd = leader({ name: "odd-1", health: "migrating" as never });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [odd]));
    renderFleet();
    const card = await findCard("odd-1");
    expect(within(card).getByText("migrating")).toBeInTheDocument();
    expect(
      within(card).getByText("The console has no figures for this leader."),
    ).toBeInTheDocument();
    expect(within(card).queryByRole("term")).not.toBeInTheDocument();
    expect(
      mock.callsTo("GET /api/leaders/odd-1/history?hours=24"),
    ).toHaveLength(0);
  });

  it("adds up the totals and says when they include old figures", async () => {
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, US])),
      "eu-1",
      "us-1",
    );
    renderFleet();
    await findCard("us-1");
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(figures(totals)).toEqual({
      "Waiting now": "6",
      "Finished, last hour": "14",
      "Finished, last day": "60",
      "Followers at work": "6",
    });
    expect(
      within(totals).getByText(
        "These include the last figures from us-1, which the console cannot check right now.",
      ),
    ).toBeInTheDocument();
  });

  it("lists what needs a look: scan errors, failed tries and a revoked credential", async () => {
    const revoked = leader({ name: "rev-1", health: "credential_revoked" });
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, revoked])),
      "eu-1",
      "rev-1",
    );
    renderFleet();
    const section = await screen.findByRole("region", { name: "Needs a look" });
    const items = within(section)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(items).toEqual([
      "eu-1 could not scan archive. the root folder is not readable",
      "rev-1 revoked this console's credential. A console administrator must replace it.",
      "rev-1 could not scan archive. the root folder is not readable",
      "2 tries failed in the last day. 1 on eu-1, 1 on rev-1.",
    ]);
    expect(
      within(section).getAllByRole("link", {
        name: "eu-1 could not scan archive.",
      })[0],
    ).toHaveAttribute("href", "/leaders/eu-1/locations");
    // It comes before the leaders' cards, so it is never below the fold.
    const first = section.parentElement?.firstElementChild;
    expect(first).toBe(section);
  });

  it("lists at most five things under Needs a look and counts the rest", async () => {
    const many = Array.from({ length: 4 }, (_, i) =>
      leader({ name: `rev-${i + 1}`, health: "credential_revoked" }),
    );
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, ...many])),
      "eu-1",
      ...many.map((l) => l.name),
    );
    renderFleet();
    const section = await screen.findByRole("region", { name: "Needs a look" });
    expect(within(section).getAllByRole("listitem")).toHaveLength(5);
    // 4 revoked + 5 scan errors (each leader carries EU's) + 1 failed-tries line = 10 in all.
    expect(concerns([EU, ...many])).toHaveLength(10);
    expect(
      within(section).getByText("And 5 more things to look at."),
    ).toBeInTheDocument();
  });

  it("keeps a leader's name whole inside Needs a look, so it never breaks at its hyphen", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const section = await screen.findByRole("region", { name: "Needs a look" });
    const names = Array.from(section.querySelectorAll("span.nowrap")).map(
      (el) => el.textContent,
    );
    expect(names).toContain("eu-1");
  });

  it("shows no Needs a look section when nothing does", async () => {
    const calm = leader({
      summary: {
        ...(EU.summary as NonNullable<typeof EU.summary>),
        failed_attempts_last_day: 0,
        scan_errors: [],
      },
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [calm])), "eu-1");
    renderFleet();
    await findCard("eu-1");
    expect(
      screen.queryByRole("region", { name: "Needs a look" }),
    ).not.toBeInTheDocument();
    expect(concerns([calm])).toEqual([]);
  });

  it("keeps the time of the last check out of every live region", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const checked = await screen.findByText(/Checked at .+, and every 10 s/);
    expect(
      checked.closest(
        "[aria-live], [role='status'], [role='alert'], [role='log']",
      ),
    ).toBeNull();
    // The count stays in a polite live region: it changes when the filter does.
    expect(
      screen
        .getByText("1 leader")
        .closest("[role='status'], [aria-live='polite']"),
    ).not.toBeNull();
  });

  it("keeps the cards when one leader's history request fails", async () => {
    const usUp = leader({
      name: "us-1",
      labels: { env: "prod", region: "us" },
    });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [EU, usUp]));
    mock.on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()));
    mock.on(
      "GET /api/leaders/us-1/history?hours=24",
      fail(502, "leader_unreachable", "down"),
    );
    renderFleet();
    const us = await findCard("us-1");
    expect(
      await within(us).findByText("No history to show"),
    ).toBeInTheDocument();
    // The card keeps its figures; only its chart is missing.
    expect(figures(us).Waiting).toBe("3");
    const eu = await findCard("eu-1");
    expect(await within(eu).findByRole("img")).toHaveAccessibleName(/eu-1/);
  });

  it("filters by label with pills and keeps the filter in the address", async () => {
    vi.useRealTimers();
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, US])),
      "eu-1",
      "us-1",
    );
    renderFleet();
    await findCard("us-1");
    const pills = screen.getByRole("group", {
      name: "Show leaders with the label",
    });
    expect(
      within(pills).getByRole("button", { name: "All leaders" }),
    ).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(
      within(pills).getByRole("button", { name: "region = us" }),
    );
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(queryCard("us-1")).toBeInTheDocument();
    expect(window.location.search).toBe("?label=region%3Dus");
    expect(screen.getByText(/1 of 2 leaders/)).toBeInTheDocument();
    expect(
      within(pills).getByRole("button", { name: "region = us" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(pills).getByRole("button", { name: "All leaders" }),
    ).toHaveAttribute("aria-pressed", "false");
    // The totals follow the filter.
    expect(
      figures(screen.getByRole("region", { name: "Totals" }))["Waiting now"],
    ).toBe("3");
    // Pressing the chosen pill again clears the filter.
    await userEvent.click(
      within(pills).getByRole("button", { name: "region = us" }),
    );
    expect(window.location.search).toBe("");
    expect(queryCard("eu-1")).toBeInTheDocument();
  });

  it("filters by a label whose value holds an equals sign", async () => {
    vi.useRealTimers();
    const odd = leader({ name: "odd-1", labels: { team: "a=b" } });
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, odd])),
      "eu-1",
      "odd-1",
    );
    renderFleet();
    await findCard("odd-1");
    await userEvent.click(screen.getByRole("button", { name: "team = a=b" }));
    expect(queryCard("odd-1")).toBeInTheDocument();
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=team%3Da%3Db");
  });

  it("offers a select instead of pills when there are many labels", async () => {
    vi.useRealTimers();
    const labels = Object.fromEntries(
      Array.from({ length: LABEL_PILL_LIMIT + 1 }, (_, i) => [`k${i}`, "v"]),
    );
    const many = leader({ name: "many-1", labels });
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, many])),
      "eu-1",
      "many-1",
    );
    renderFleet();
    await findCard("many-1");
    expect(
      screen.queryByRole("group", { name: "Show leaders with the label" }),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Label" }),
      "k0=v",
    );
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=k0%3Dv");
  });

  it("keeps a label from the address that no leader has, so it can be cleared", async () => {
    vi.useRealTimers();
    window.history.replaceState(null, "", "/?label=region%3Dmoon");
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    expect(
      await screen.findByText("No leader has the label region=moon."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "region = moon" }),
    ).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "All leaders" }));
    expect(await findCard("eu-1")).toBeInTheDocument();
  });

  it("keeps the last figures and says so when a check fails", async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The real clock is long past the fixed NOW; without this the person counts as idle
    // and polling is paused (usePoll skips refreshes while idle).
    setLastInputForTests(Date.now());
    let n = 0;
    withHistory(
      mockFetch().on("GET /api/fleet", () =>
        ++n === 1 ? reply(200, [EU]) : fail(503, "unavailable", "down"),
      ),
      "eu-1",
    );
    renderFleet();
    expect(await findCard("eu-1")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /^The last check did not work: .+ These are the last figures\.$/,
    );
    expect(queryCard("eu-1")).toBeInTheDocument();
  });

  it("says when the person sees no leaders", async () => {
    mockFetch().on("GET /api/fleet", reply(200, []));
    renderFleet();
    expect(
      await screen.findByText(/You have no role on any leader yet/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Totals" }),
    ).not.toBeInTheDocument();
  });

  it("shows the error with a retry when the first load fails", async () => {
    mockFetch().on(
      "GET /api/fleet",
      fail(503, "unavailable", "service temporarily unavailable"),
    );
    renderFleet();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "temporarily unavailable",
    );
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("keeps one heading element from loading to loaded, so focus on it is not lost", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    withHistory(
      mockFetch().on("GET /api/fleet", async () => {
        await gate;
        return reply(200, [EU]);
      }),
      "eu-1",
    );
    renderFleet();
    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "Fleet",
    });
    expect(screen.getByRole("status")).toHaveTextContent("Loading the fleet…");
    heading.tabIndex = -1;
    heading.focus();
    release();
    await findCard("eu-1");
    expect(screen.getByRole("heading", { level: 1, name: "Fleet" })).toBe(
      heading,
    );
    expect(heading).toHaveFocus();
  });

  it("uses no table and no style attribute", async () => {
    withHistory(
      mockFetch().on("GET /api/fleet", reply(200, [EU, US])),
      "eu-1",
      "us-1",
    );
    const { container } = renderFleet();
    await findCard("us-1");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });
});
