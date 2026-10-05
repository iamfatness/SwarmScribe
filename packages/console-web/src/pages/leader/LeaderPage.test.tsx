import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { reply } from "../../test/fetchMock";
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
      await screen.findByText(
        /Actions that need a higher role are shown disabled/,
      ),
    ).toHaveTextContent("Your role on eu-1: operator.");
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
      await screen.findByText(/This leader is not visible to you/),
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
      await screen.findByText(/The console cannot reach eu-1/),
    ).toHaveTextContent("The last successful poll was at");
    expect(
      screen.getByRole("heading", { level: 1, name: "eu-1" }),
    ).toBeInTheDocument();
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

