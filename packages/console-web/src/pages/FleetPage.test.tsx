import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLastInputForTests } from "../app/activity";
import { FleetProvider } from "../app/fleet";
import { RouterProvider } from "../app/router";
import { fail, mockFetch, reply, type FetchMock } from "../test/fetchMock";
import { NOW, STATUS, history, leader } from "../test/fixtures";
import { FleetPage } from "./FleetPage";

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
  for (const name of names) mock.on(`GET /api/leaders/${name}/history?hours=24`, reply(200, history()));
  return mock;
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

  it("shows one row per leader with the overview figures", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const row = (await screen.findByRole("rowheader", { name: /eu-1/ })).closest("tr") as HTMLElement;
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells.slice(0, 8)).toEqual([
      "Reachable",
      "3",
      "7",
      "30",
      "1",
      "default 2 · gpu 1",
      // 420 s at the snapshot, taken 10 s before NOW.
      "7 min",
      "archive: the root folder is not readable",
    ]);
    expect(screen.getByRole("columnheader", { name: "Oldest queued job (since created)" })).toBeInTheDocument();
    expect(await within(row).findByRole("img")).toHaveAccessibleName(/Jobs completed per hour/);
  });

  it("shows an unreachable leader with stale figures and keeps the others working", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    const usRow = (await screen.findByRole("rowheader", { name: /us-1/ })).closest("tr") as HTMLElement;
    expect(usRow).toHaveClass("is-stale");
    expect(within(usRow).getByText("Unreachable")).toBeInTheDocument();
    expect(within(usRow).getByText(/Figures as of/)).toBeInTheDocument();
    expect(within(usRow).getByText("Last error: connect_error")).toBeInTheDocument();
    const euRow = screen.getByRole("rowheader", { name: /eu-1/ }).closest("tr") as HTMLElement;
    expect(within(euRow).getByText("Reachable")).toBeInTheDocument();
  });

  it("shows a revoked leader row with its snapshot time", async () => {
    const revoked = leader({ name: "rev-1", health: "credential_revoked" });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [revoked])), "rev-1");
    renderFleet();
    const row = (await screen.findByRole("rowheader", { name: /rev-1/ })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Credential revoked")).toBeInTheDocument();
    expect(within(row).getByText(/Figures as of/)).toBeInTheDocument();
  });

  it("shows a leader that never answered without figures", async () => {
    const pending = leader({
      name: "new-1",
      health: "pending",
      consecutive_failures: 2,
      summary: null,
      snapshot: null,
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [pending])), "new-1");
    renderFleet();
    const row = (await screen.findByRole("rowheader", { name: /new-1/ })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Not answering yet (2 failed polls)")).toBeInTheDocument();
    expect(within(row).getByText("No successful poll yet")).toBeInTheDocument();
  });

  it("names the table and keeps it in a focusable scroll region", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const region = await screen.findByRole("region", { name: "Leaders" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(within(region).getByRole("table", { name: /Leaders/ })).toBeInTheDocument();
  });

  it("keeps the table when one leader history request fails", async () => {
    const mock = mockFetch().on("GET /api/fleet", reply(200, [EU, US]));
    mock.on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()));
    mock.on("GET /api/leaders/us-1/history?hours=24", fail(502, "leader_unreachable", "down"));
    renderFleet();
    expect(await screen.findByText("History unavailable")).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: /eu-1/ })).toBeInTheDocument();
    expect(await screen.findByRole("img")).toHaveAccessibleName(/eu-1/);
  });

  it("filters by label and keeps the filter in the address", async () => {
    vi.useRealTimers();
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    await screen.findByRole("rowheader", { name: /us-1/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Label" }), "region=us");
    expect(screen.queryByRole("rowheader", { name: /eu-1/ })).not.toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: /us-1/ })).toBeInTheDocument();
    expect(window.location.search).toBe("?label=region%3Dus");
    expect(screen.getByText(/1 of 2 leaders/)).toBeInTheDocument();
  });

  it("filters by a label whose value holds an equals sign", async () => {
    vi.useRealTimers();
    const odd = leader({ name: "odd-1", labels: { team: "a=b" } });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, odd])), "eu-1", "odd-1");
    renderFleet();
    await screen.findByRole("rowheader", { name: /odd-1/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Label" }), "team=a=b");
    expect(screen.getByRole("rowheader", { name: /odd-1/ })).toBeInTheDocument();
    expect(screen.queryByRole("rowheader", { name: /eu-1/ })).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=team%3Da%3Db");
  });

  it("keeps the last figures and says so when a refresh fails", async () => {
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
    expect(await screen.findByRole("rowheader", { name: /eu-1/ })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not refresh the fleet");
    expect(screen.getByRole("rowheader", { name: /eu-1/ })).toBeInTheDocument();
  });

  it("says when the person sees no leaders", async () => {
    mockFetch().on("GET /api/fleet", reply(200, []));
    renderFleet();
    expect(await screen.findByText(/You hold no role on any leader yet/)).toBeInTheDocument();
  });

  it("shows the error with a retry when the first load fails", async () => {
    mockFetch().on("GET /api/fleet", fail(503, "unavailable", "service temporarily unavailable"));
    renderFleet();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
