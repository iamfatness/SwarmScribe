import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { SESSION } from "../test/fixtures";

// The sign-in page sits outside the session: these render the whole app at /sign-in.

describe("the sign-in page", () => {
  it("lists the providers with a safe return_to and makes no session-bound calls", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%3Flabel%3Denv%253Dprod");
    const mock = mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra", "google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Continue with Microsoft" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=entra&return_to=%2F%3Flabel%3Denv%253Dprod");
    expect(screen.getByRole("link", { name: "Continue with Google" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign in");
    expect(screen.queryByText(/already signed in/)).not.toBeInTheDocument();
    expect(mock.callsTo("GET /api/fleet")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts the sign-in card in the main region and the brand beside it", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    const { container } = render(<App />);
    const main = within(await screen.findByRole("main"));
    expect(main.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(await main.findByRole("link", { name: "Continue with Microsoft" })).toBeInTheDocument();
    expect(screen.getByText("Every leader you look after, in one place.")).toBeInTheDocument();
    expect(screen.getByText(window.location.host)).toBeInTheDocument();
    // Nothing on this page needs a session: no rail, no sign-out.
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("names a provider it does not know by its own id", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["okta"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    expect(await screen.findByRole("link", { name: "Continue with okta" })).toHaveAttribute(
      "href",
      "/auth/login?provider=okta",
    );
  });

  it("says so when no way to sign in is set up", async () => {
    window.history.replaceState(null, "", "/sign-in?signed_out=1");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: [] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    expect(await screen.findByText(/No way to sign in is set up on this console/)).toBeInTheDocument();
    expect(screen.getByText("You are signed out.")).toHaveAttribute("role", "status");
  });

  it("says so in the card when the ways to sign in cannot be loaded", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", fail(503, "unavailable", "down"))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const main = within(await screen.findByRole("main"));
    expect(await main.findByRole("alert")).toBeInTheDocument();
    expect(main.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(main.queryByRole("link", { name: /^Continue with/ })).not.toBeInTheDocument();
    expect(main.queryByText(/^Loading/)).not.toBeInTheDocument();
  });

  it("drops a hostile return_to", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%2Fevil.example");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Continue with Google" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=google");
  });

  it("offers a way back to a person who is already signed in", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", reply(200, SESSION));
    render(<App />);
    expect(await screen.findByText(/already signed in as person@example.org/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the console" })).toHaveAttribute("href", "/");
  });
});
