import { render, screen } from "@testing-library/react";
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
});
