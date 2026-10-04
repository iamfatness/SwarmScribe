import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { resetSessionEndedForTests } from "./app/navigation";
import * as navigation from "./app/navigation";
import { fail, mockFetch, reply } from "./test/fetchMock";
import { SESSION, history, leader } from "./test/fixtures";

afterEach(() => {
  resetSessionEndedForTests();
  vi.restoreAllMocks();
});

function signedInConsole() {
  return mockFetch()
    .on("GET /api/session", reply(200, SESSION))
    .on("GET /api/fleet", reply(200, [leader()]))
    .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
    .on("POST /api/session/logout", reply(204));
}

describe("the signed-in shell", () => {
  it("has a skip link, the person, a theme switch and sign-out", async () => {
    signedInConsole();
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    expect(screen.getByText("person@example.org")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fleet", current: "page" })).toBeInTheDocument();
  });

  it("signs out, leaving the app even when the logout request fails", async () => {
    signedInConsole().on("POST /api/session/logout", fail(500, "internal", "boom"));
    const out = vi.spyOn(navigation, "goToSignedOut").mockImplementation(() => undefined);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(out).toHaveBeenCalledTimes(1));
  });

  it("shows the not-found page and moves focus to its heading after navigating there", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    window.history.pushState(null, "", "/nowhere");
    window.dispatchEvent(new PopStateEvent("popstate"));
    const heading = await screen.findByRole("heading", { level: 1, name: "Page not found" });
    await vi.waitFor(() => expect(heading).toHaveFocus());
    await userEvent.click(screen.getByRole("link", { name: "Go to the fleet overview" }));
    const fleet = await screen.findByRole("heading", { level: 1, name: "Fleet" });
    await vi.waitFor(() => expect(fleet).toHaveFocus());
  });
});

describe("the sign-in page", () => {
  it("lists the providers with a safe return_to and makes no session-bound calls", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%3Flabel%3Denv%253Dprod");
    const mock = mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra", "google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Sign in with Microsoft Entra ID" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=entra&return_to=%2F%3Flabel%3Denv%253Dprod");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign in");
    expect(screen.queryByText(/already signed in/)).not.toBeInTheDocument();
    expect(mock.callsTo("GET /api/fleet")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("drops a hostile return_to", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%2Fevil.example");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Sign in with Google" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=google");
  });

  it("offers a way back to a person who is already signed in", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", reply(200, SESSION));
    render(<App />);
    expect(await screen.findByText(/already signed in as person@example.org/)).toBeInTheDocument();
  });
});
