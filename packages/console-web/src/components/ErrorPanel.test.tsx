import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { CurrentLeader } from "../app/currentLeader";
import { RouterProvider } from "../app/router";
import { leader } from "../test/fixtures";
import { ErrorPanel } from "./ErrorPanel";

describe("ErrorPanel", () => {
  it("shows an unknown code's text as text beneath a title of the console's own", () => {
    render(<ErrorPanel error={new ApiError(409, "brand_new", "it <b>clashed</b> with <img src=x>")} />);
    const alert = screen.getByRole("alert");
    expect(alert.querySelector(".error-title")).toHaveTextContent(
      "That no longer fits how things stand. Refresh, then look again.",
    );
    expect(alert.querySelector(".error-detail")).toHaveTextContent(
      "The answer said: it <b>clashed</b> with <img src=x>",
    );
    expect(alert.querySelector("b, img")).toBeNull();
  });

  it("shows nothing of the server's sentence for a code the console knows", () => {
    render(
      <ErrorPanel
        error={new ApiError(409, "last_admin", "this is the last console administrator; add another first")}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The last console administrator cannot be removed.");
    expect(alert).toHaveTextContent("Add another one first.");
    expect(alert).not.toHaveTextContent(/this is the last|add another first/);
  });

  it("names the leader of the page it is on", () => {
    render(
      <CurrentLeader.Provider value={leader({ name: "us-1" })}>
        <ErrorPanel error={new ApiError(503, "leader_unreachable", "leader us-1 cannot be reached", 15)} />
      </CurrentLeader.Provider>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.querySelector(".error-title")).toHaveTextContent(/^us-1 is not answering right now\.$/);
    expect(alert.querySelector(".error-detail")).toHaveTextContent(/^Try again in 15 seconds\.$/);
  });

  it("offers the retry only where asking again can work", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <ErrorPanel error={new ApiError(503, "leader_unreachable", "x")} onRetry={onRetry} />,
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    rerender(<ErrorPanel error={new ApiError(409, "leader_disabled", "x")} onRetry={onRetry} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A console administrator can switch it on under Administration.",
    );
  });

  it("links back to the fleet when the leader is not there for the person", () => {
    render(
      <RouterProvider>
        <ErrorPanel error={new ApiError(404, "leader_not_found", "no leader with that name")} onRetry={vi.fn()} />
      </RouterProvider>,
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByRole("link", { name: "Go back to the fleet" })).toHaveAttribute("href", "/");
    expect(alert).not.toHaveTextContent("no leader with that name");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });
});
