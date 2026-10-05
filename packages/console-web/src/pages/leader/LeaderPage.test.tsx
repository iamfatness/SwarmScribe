import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fail, reply } from "../../test/fetchMock";
import { history, leader } from "../../test/fixtures";
import type { JobOut } from "../../api/types";
import { renderApp } from "../../test/renderApp";

const JOB: JobOut = {
  id: "11111111-1111-4111-8111-111111111111",
  state: "failed",
  location: "intake",
  key: "a.wav",
  priority: 0,
  attempts: 1,
  max_attempts: 3,
  pool: "default",
  leased_by: null,
  failure_reason: null,
  cancelled_by: null,
  no_speech: null,
  created_at: "2026-10-04T11:00:00Z",
  completed_at: null,
};

describe("leader drill-down", () => {
  it("shows the leader, the person's role and the tabs", async () => {
    renderApp("/leaders/eu-1/pools").on(
      "GET /api/leaders/eu-1/followers",
      reply(200, []),
    );
    expect(
      await screen.findByRole("heading", { level: 1, name: "eu-1" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/What needs an admin is shown, but switched off/),
    ).toHaveTextContent("You are an operator here. What needs an admin is shown, but switched off.");
    expect(
      within(screen.getByRole("main")).getByRole("list", { name: "Labels" }),
    ).toHaveTextContent("env=prodregion=eu");
    const tabs = screen.getByRole("navigation", { name: "eu-1 sections" });
    const pools = within(tabs).getByRole("link", {
      name: "Pools and followers",
    });
    expect(pools).toHaveAttribute("aria-current", "page");
    expect(pools).toHaveAttribute("href", "/leaders/eu-1/pools");
  });

  it("says a leader the person cannot see is not visible to them", async () => {
    renderApp("/leaders/secret-1/pools");
    expect(
      await screen.findByText(/You cannot see this leader/),
    ).toBeInTheDocument();
  });

  it("moves focus to the leader's heading when the page changes from the fleet to a leader", async () => {
    renderApp("/")
      .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
      .on("GET /api/leaders/eu-1/followers", reply(200, []));
    const fleet = within(await screen.findByRole("main"));
    await userEvent.click(await fleet.findByRole("link", { name: "eu-1" }));
    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "eu-1",
    });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it("tells each role what is switched off for it", async () => {
    renderApp("/leaders/eu-1/pools", { fleet: [leader({ role: "viewer" })] }).on(
      "GET /api/leaders/eu-1/followers",
      reply(200, []),
    );
    expect(await screen.findByText(/You are a/)).toHaveTextContent(
      "You are a viewer here. What needs an operator or an admin is shown, but switched off.",
    );
  });

  it("redirects a bare leader URL to its first tab", async () => {
    renderApp("/leaders/eu-1").on(
      "GET /api/leaders/eu-1/followers",
      reply(200, []),
    );
    expect(
      await screen.findByRole("heading", { level: 1, name: "eu-1" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(window.location.pathname).toBe("/leaders/eu-1/pools"),
    );
  });

  it("still shows an unreachable leader, with its state and last snapshot time", async () => {
    renderApp("/leaders/eu-1/pools", {
      fleet: [leader({ health: "unreachable", consecutive_failures: 3 })],
    }).on(
      "GET /api/leaders/eu-1/followers",
      reply(503, { code: "leader_unreachable", message: "down" }),
    );
    expect(
      await screen.findByText(/eu-1 is not answering, so nothing here can be read or changed/),
    ).toHaveTextContent("It last answered at");
    expect(within(screen.getByRole("main")).getByText("Not answering")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "eu-1" }),
    ).toBeInTheDocument();
  });

  it("says a leader the fleet knows is down once more only, quietly, with the wait and a retry", async () => {
    const mock = renderApp("/leaders/us-1/jobs", {
      fleet: [leader({ name: "us-1", health: "unreachable", consecutive_failures: 3 })],
    }).on("GET /api/leaders/us-1/jobs?limit=100", {
      status: 503,
      body: { code: "leader_unreachable", message: "leader us-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    const line = await screen.findByText(/Nothing to show until us-1 answers/);
    expect(line).toHaveTextContent("Nothing to show until us-1 answers. Try again in 15 seconds.");
    expect(line).toHaveAttribute("role", "status");
    // The notice above has said it: no second, red panel, and none of the backend's words.
    const main = screen.getByRole("main");
    expect(within(main).queryByRole("alert")).not.toBeInTheDocument();
    expect(main).not.toHaveTextContent(/cannot be reached/);
    expect(main.textContent?.match(/not answering/gi)).toHaveLength(2); // the pill and the notice
    const before = mock.callsTo("GET /api/leaders/us-1/jobs?limit=100").length;
    await userEvent.click(within(main).getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(mock.callsTo("GET /api/leaders/us-1/jobs?limit=100").length).toBe(before + 1),
    );
  });

  it("names the leader in the error when the fleet has not noticed yet that it is down", async () => {
    renderApp("/leaders/eu-1/jobs").on("GET /api/leaders/eu-1/jobs?limit=100", {
      status: 503,
      body: { code: "leader_unreachable", message: "leader eu-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("eu-1 is not answering right now.");
    expect(alert).toHaveTextContent("Try again in 15 seconds.");
    expect(alert).not.toHaveTextContent(/cannot be reached/);
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("never shows the backend's word for a switched-off leader, and offers no retry that cannot work", async () => {
    renderApp("/leaders/us-1/locations", {
      fleet: [leader({ name: "us-1", role: "admin", health: "disabled", enabled: false })],
    }).on("GET /api/leaders/us-1/locations", fail(409, "leader_disabled", "this leader is disabled in the console"));
    const line = await screen.findByText(/Nothing to show while us-1 is switched off/);
    expect(line).toHaveTextContent(
      "Nothing to show while us-1 is switched off. A console administrator can switch it on under Administration.",
    );
    const main = screen.getByRole("main");
    expect(main).not.toHaveTextContent(/disabled/i);
    expect(within(main).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(main).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("says a switched-off leader in its own words when the fleet has not caught up", async () => {
    renderApp("/leaders/us-1/locations", { fleet: [leader({ name: "us-1" })] }).on(
      "GET /api/leaders/us-1/locations",
      fail(409, "leader_disabled", "this leader is disabled in the console"),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("us-1 is switched off in the console.");
    expect(alert).toHaveTextContent("A console administrator can switch it on under Administration.");
    expect(alert).not.toHaveTextContent(/disabled/i);
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("sends a person back to the fleet when the leader is removed while its page is open", async () => {
    renderApp("/leaders/eu-1/jobs").on(
      "GET /api/leaders/eu-1/jobs?limit=100",
      fail(404, "leader_not_found", "no leader with that name"),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You cannot see this leader.");
    expect(alert).toHaveTextContent(
      "It is no longer in the console, or you no longer have a role on it. Go back to the fleet.",
    );
    expect(alert).not.toHaveTextContent(/no leader with that name/);
    expect(within(alert).getByRole("link", { name: "Go back to the fleet" })).toHaveAttribute("href", "/");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("promises an admin nothing the leader may not allow: us-1 lets the console act only as an operator", async () => {
    renderApp("/leaders/us-1/tokens", { fleet: [leader({ name: "us-1", role: "admin" })] }).on(
      "GET /api/leaders/us-1/tokens",
      fail(403, "forbidden", "this needs the admin role; console main is limited to operator"),
    );
    const role = await screen.findByText(/You are an/);
    expect(role).toHaveTextContent(/^You are an admin here\.$/);
    expect(screen.getByRole("main")).not.toHaveTextContent(/Nothing here is switched off/);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("us-1 lets this console act only as an operator.");
    expect(alert).toHaveTextContent("This needs an admin. Whoever runs the leader can change that.");
    expect(alert).not.toHaveTextContent(/limited to|needs the admin role/);
  });

  it("keeps focus on the tab link when only the tab changes", async () => {
    renderApp("/leaders/eu-1/pools")
      .on("GET /api/leaders/eu-1/followers", reply(200, []))
      .on("GET /api/leaders/eu-1/jobs?limit=100", reply(200, []));
    const tabs = await screen.findByRole("navigation", { name: "eu-1 sections" });
    const jobs = within(tabs).getByRole("link", { name: "Jobs" });
    await userEvent.click(jobs);
    await waitFor(() => expect(jobs).toHaveAttribute("aria-current", "page"));
    expect(jobs).toHaveFocus();
    expect(screen.getByRole("heading", { level: 1, name: "eu-1" })).not.toHaveFocus();
  });

  it("loads the tab of a leader whose name has a dot", async () => {
    renderApp("/leaders/eu.west-1/jobs", { fleet: [leader({ name: "eu.west-1" })] }).on(
      "GET /api/leaders/eu.west-1/jobs?limit=100",
      reply(200, []),
    );
    expect(await screen.findByText("This leader has no jobs.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "eu.west-1" })).toBeInTheDocument();
  });

  it("answers an unknown tab with the not-found page", async () => {
    renderApp("/leaders/eu-1/nonsense");
    expect(await screen.findByRole("heading", { name: /not found/i })).toBeInTheDocument();
  });

  it("does not apply a slow answer for a leader the person has left", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderApp("/leaders/eu-1/jobs", { fleet: [leader(), leader({ name: "us-1" })] })
      .on("GET /api/leaders/eu-1/jobs?limit=100", async () => {
        await gate;
        return reply(200, [{ ...JOB, key: "eu-only.wav" }]);
      })
      .on("GET /api/leaders/us-1/jobs?limit=100", reply(200, []))
      .on("GET /api/leaders/us-1/followers", reply(200, []))
      .on("GET /api/leaders/eu-1/followers", reply(200, []));
    const page = within(await screen.findByRole("main"));
    await userEvent.click(await page.findByRole("link", { name: "Fleet" }));
    await userEvent.click(await page.findByRole("link", { name: "us-1" }));
    await userEvent.click(await screen.findByRole("link", { name: "Jobs" }));
    expect(await screen.findByText("This leader has no jobs.")).toBeInTheDocument();
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("eu-only.wav")).not.toBeInTheDocument();
    expect(screen.getByText("This leader has no jobs.")).toBeInTheDocument();
  });
});

