import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { resetSessionEndedForTests } from "./app/navigation";
import * as navigation from "./app/navigation";
import { roleSummary } from "./components/Layout";
import { fail, mockFetch, reply } from "./test/fetchMock";
import { SESSION, history, leader } from "./test/fixtures";
import { renderApp } from "./test/renderApp";

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

const DOWN = leader({ name: "us-1", health: "unreachable", consecutive_failures: 3 });

describe("the signed-in shell", () => {
  it("has a skip link, the brand, the person, a theme switch and sign-out", async () => {
    signedInConsole();
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    const banner = within(screen.getByRole("banner"));
    expect(banner.getByRole("link", { name: "SwarmScribe console" })).toHaveAttribute("href", "/");
    expect(banner.getByText("person@example.org")).toBeInTheDocument();
    expect(await banner.findByText("Operator on 1 leader")).toBeInTheDocument();
    expect(banner.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(banner.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(banner.getByRole("link", { name: "Fleet", current: "page" })).toBeInTheDocument();
  });

  it("comes before the main region and holds the skip link's target", async () => {
    signedInConsole();
    const { container } = render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "main");
    expect(main).toHaveAttribute("tabindex", "-1");
    const banner = screen.getByRole("banner");
    expect(banner.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("lists each visible leader under Fleet and marks the one being looked at", async () => {
    renderApp("/leaders/eu-1/pools", { fleet: [leader(), DOWN] }).on(
      "GET /api/leaders/eu-1/followers",
      reply(200, []),
    );
    const nav = within(await screen.findByRole("navigation", { name: "Console" }));
    const leaders = within(await nav.findByRole("list", { name: "Leaders" }));
    expect(leaders.getAllByRole("link").map((link) => link.textContent)).toEqual(["eu-1", "us-1 no answer"]);
    expect(leaders.getByRole("link", { name: "eu-1" })).toHaveAttribute("aria-current", "page");
    expect(leaders.getByRole("link", { name: "eu-1" })).toHaveAttribute("href", "/leaders/eu-1/pools");
    expect(leaders.getByRole("link", { name: "us-1 no answer" })).not.toHaveAttribute("aria-current");
    expect(nav.getByRole("link", { name: "Fleet" })).not.toHaveAttribute("aria-current");
    // A viewer of leaders is not offered Administration.
    expect(nav.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
  });

  it("offers Administration to a console administrator and marks it when there", async () => {
    renderApp("/admin/leaders", { session: { ...SESSION, console_admin: true } }).on(
      "GET /api/admin/leaders",
      reply(200, []),
    );
    const banner = within(await screen.findByRole("banner"));
    expect(await banner.findByRole("link", { name: "Administration" })).toHaveAttribute("aria-current", "page");
    expect(banner.getByRole("link", { name: "Administration" })).toHaveAttribute("href", "/admin/leaders");
    expect(banner.getByText("Console administrator")).toBeInTheDocument();
    expect(banner.getByRole("link", { name: "Fleet" })).not.toHaveAttribute("aria-current");
  });

  it("opens and closes the menu from its button, and Escape hands focus back to it", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    const panel = document.getElementById(menu.getAttribute("aria-controls") ?? "");
    expect(panel).not.toBeNull();
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("data-open", "false");

    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveAttribute("data-open", "true");
    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(menu);
    await userEvent.tab();
    await userEvent.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
  });

  it("moves focus into the menu when it opens and stops the page behind it scrolling", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(document.documentElement).not.toHaveClass("menu-open");
    await userEvent.click(menu);
    const nav = within(screen.getByRole("navigation", { name: "Console" }));
    expect(nav.getByRole("link", { name: "Fleet" })).toHaveFocus();
    expect(document.documentElement).toHaveClass("menu-open");
    await userEvent.click(menu);
    expect(document.documentElement).not.toHaveClass("menu-open");
  });

  it("closes the menu on a press outside it, but not on a press inside it", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(menu);
    await userEvent.click(screen.getByText("person@example.org"));
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(screen.getByRole("heading", { level: 1, name: "Fleet" }));
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(document.documentElement).not.toHaveClass("menu-open");
  });

  it("closes the menu when a link in it is followed", async () => {
    renderApp("/", { fleet: [leader()] })
      .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
      .on("GET /api/leaders/eu-1/followers", reply(200, []));
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(menu);
    const nav = within(screen.getByRole("navigation", { name: "Console" }));
    await userEvent.click(await nav.findByRole("link", { name: "eu-1" }));
    await screen.findByRole("heading", { level: 1, name: "eu-1" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("still offers the brand, the theme and sign-out when the fleet cannot be loaded", async () => {
    mockFetch()
      .on("GET /api/session", reply(200, SESSION))
      .on("GET /api/fleet", fail(503, "unavailable", "down"));
    render(<App />);
    expect(await within(await screen.findByRole("main")).findByRole("alert")).toBeInTheDocument();
    const banner = within(screen.getByRole("banner"));
    expect(banner.getByRole("link", { name: "Fleet", current: "page" })).toBeInTheDocument();
    expect(banner.queryByRole("list", { name: "Leaders" })).not.toBeInTheDocument();
    // No fleet, so no claim about roles: only who is signed in.
    expect(banner.getByText("person@example.org")).toBeInTheDocument();
    expect(banner.queryByText(/leader/)).not.toBeInTheDocument();
    expect(banner.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(banner.getByRole("button", { name: "Sign out" })).toBeEnabled();
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

describe("roleSummary", () => {
  it("says which roles the person holds, highest first", () => {
    expect(roleSummary([])).toBe("No role on any leader yet");
    expect(roleSummary([leader({ role: "admin" })])).toBe("Admin on 1 leader");
    expect(roleSummary([leader({ role: "admin" }), leader({ role: "admin" })])).toBe("Admin on 2 leaders");
    expect(
      roleSummary([leader({ role: "viewer" }), leader({ role: "admin" }), leader({ role: "admin" })]),
    ).toBe("Admin on 2, viewer on 1 leader");
    expect(
      roleSummary([leader({ role: "viewer" }), leader({ role: "operator" }), leader({ role: "admin" })]),
    ).toBe("Admin on 1, operator on 1, viewer on 1 leader");
  });
});
