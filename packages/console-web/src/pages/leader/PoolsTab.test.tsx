import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { FollowerOut } from "../../api/types";
import { reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

const FOLLOWER: FollowerOut = {
  id: "33333333-3333-4333-8333-333333333333",
  pool: "default",
  state: "active",
  device: "cuda",
  last_seen_at: "2026-10-04T11:59:40Z",
  created_at: "2026-10-01T00:00:00Z",
  leases: 1,
};

describe("pools and followers tab", () => {
  it("shows pools from the latest poll and drains a follower", async () => {
    let drained = false;
    const mock = renderApp("/leaders/eu-1/pools")
      .on("GET /api/leaders/eu-1/followers", () =>
        reply(200, [{ ...FOLLOWER, state: drained ? "draining" : "active" }]),
      )
      .on(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`, () => {
        drained = true;
        return reply(200, { ...FOLLOWER, state: "draining" });
      });
    const pools = await screen.findByRole("region", { name: "Pools" });
    expect(
      within(pools).getByRole("rowheader", { name: "gpu" }),
    ).toBeInTheDocument();
    await userEvent.click(
      await screen.findByRole("button", { name: "Drain follower 33333333" }),
    );
    expect(
      await screen.findByText("Follower 33333333 is draining."),
    ).toBeInTheDocument();
    expect(
      mock.callsTo(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`)[0]
        ?.headers["X-CSRF-Token"],
    ).toBe("csrf-token-1");
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Drain follower 33333333" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("asks before revoking, then reports the released jobs and refreshes", async () => {
    let revoked = false;
    const mock = renderApp("/leaders/eu-1/pools", {
      fleet: [leader({ role: "admin" })],
    })
      .on("GET /api/leaders/eu-1/followers", () =>
        reply(200, revoked ? [] : [FOLLOWER]),
      )
      .on(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/revoke`, () => {
        revoked = true;
        return reply(200, { id: FOLLOWER.id, state: "revoked", released: 2 });
      });
    await userEvent.click(
      await screen.findByRole("button", { name: "Revoke follower 33333333" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Revoke follower 33333333?",
    });
    expect(
      mock.callsTo(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/revoke`),
    ).toHaveLength(0);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Revoke follower" }),
    );
    expect(
      await screen.findByText(
        /is revoked; 2 leased jobs went back to the queue/,
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Revoke follower 33333333" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows revoke disabled with the role it needs for an operator, and announces a failed drain", async () => {
    renderApp("/leaders/eu-1/pools")
      .on("GET /api/leaders/eu-1/followers", reply(200, [FOLLOWER]))
      .on(
        `POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`,
        reply(503, { code: "leader_unreachable", message: "down" }),
      );
    const revoke = await screen.findByRole("button", {
      name: "Revoke follower 33333333",
    });
    expect(revoke).toBeDisabled();
    expect(revoke).toHaveAccessibleDescription("needs admin");
    await userEvent.click(
      screen.getByRole("button", { name: "Drain follower 33333333" }),
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
