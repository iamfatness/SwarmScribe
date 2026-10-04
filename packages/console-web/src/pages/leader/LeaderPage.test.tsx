import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { reply } from "../../test/fetchMock";
import { history, leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

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
    await userEvent.click(await screen.findByRole("link", { name: "eu-1" }));
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
});
