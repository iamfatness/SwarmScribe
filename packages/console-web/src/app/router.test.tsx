import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Link, RouterProvider, matchPath, useLocation } from "./router";

function Where() {
  const { pathname, search } = useLocation();
  return <p>at {pathname + search}</p>;
}

describe("router", () => {
  it("matches patterns and decodes parameters", () => {
    expect(matchPath("/leaders/:name/:tab", "/leaders/eu-1.prod/jobs")).toEqual({
      name: "eu-1.prod",
      tab: "jobs",
    });
    expect(matchPath("/leaders/:name/:tab", "/leaders/a%20b/jobs")).toEqual({ name: "a b", tab: "jobs" });
    expect(matchPath("/leaders/:name/:tab", "/leaders/eu-1")).toBeNull();
    expect(matchPath("/leaders/:name/:tab", "/leaders//jobs")).toBeNull();
    expect(matchPath("/leaders/:name/:tab", "/leaders/%E0%A4%A/jobs")).toBeNull();
    expect(matchPath("/", "/")).toEqual({});
  });

  it("navigates on a plain click without reloading", async () => {
    render(
      <RouterProvider>
        <Link to="/leaders/eu-1/jobs?state=failed">Jobs</Link>
        <Where />
      </RouterProvider>,
    );
    await userEvent.click(screen.getByRole("link", { name: "Jobs" }));
    expect(screen.getByText("at /leaders/eu-1/jobs?state=failed")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/leaders/eu-1/jobs");
  });

  it("follows the browser's back and forward buttons", async () => {
    render(
      <RouterProvider>
        <Where />
      </RouterProvider>,
    );
    window.history.pushState(null, "", "/elsewhere?x=1");
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("at /elsewhere?x=1")).toBeInTheDocument();
  });

  it("leaves modified clicks to the browser (new tab, new window)", async () => {
    render(
      <RouterProvider>
        <Link to="/leaders/eu-1/jobs">Jobs</Link>
        <Where />
      </RouterProvider>,
    );
    const link = screen.getByRole("link", { name: "Jobs" });
    for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }]) {
      // fireEvent.click returns false when something called preventDefault.
      expect(fireEvent.click(link, modifier)).toBe(true);
    }
    expect(fireEvent.click(link, { button: 1 })).toBe(true);
    expect(screen.getByText("at /")).toBeInTheDocument();
  });
});
