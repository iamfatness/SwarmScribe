import { render, screen } from "@testing-library/react";
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
