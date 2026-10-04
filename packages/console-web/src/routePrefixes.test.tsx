import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "./test/renderApp";

// The list in app/routePrefixes.json is the only thing that makes a path a route: with
// /admin taken out of it, the admin page is not found, though App.tsx still has the page.
vi.mock("./app/routePrefixes.json", () => ({ default: ["/", "/leaders", "/sign-in"] }));

describe("the route prefixes list", () => {
  it("sends a path whose first segment is not listed to the not-found page", async () => {
    renderApp("/admin/leaders");
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  });

  it("still serves a listed route", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
  });
});
