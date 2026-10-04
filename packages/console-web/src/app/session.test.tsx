import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { SESSION } from "../test/fixtures";
import * as navigation from "./navigation";
import { SessionProvider, useSession } from "./session";

function Who() {
  const { session, signOut } = useSession();
  return (
    <>
      <p>signed in as {session.email}</p>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
    </>
  );
}

describe("SessionProvider", () => {
  it("loads the session and sends its CSRF token on later unsafe calls", async () => {
    const mock = mockFetch().on("GET /api/session", reply(200, SESSION)).on("POST /api/x", reply(200, {}));
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    expect(await screen.findByText("signed in as person@example.org")).toBeInTheDocument();
    await api.post("/api/x");
    expect(mock.callsTo("POST /api/x")[0]?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
  });

  it("sends a person without a session to sign in", async () => {
    mockFetch().on("GET /api/session", fail(401, "unauthenticated"));
    const goToSignIn = vi.spyOn(navigation, "goToSignIn").mockImplementation(() => undefined);
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await vi.waitFor(() => expect(goToSignIn).toHaveBeenCalled());
  });

  it("signs out with the CSRF token and leaves for the signed-out page", async () => {
    const mock = mockFetch()
      .on("GET /api/session", reply(200, SESSION))
      .on("POST /api/session/logout", reply(204));
    const goToSignedOut = vi.spyOn(navigation, "goToSignedOut").mockImplementation(() => undefined);
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(goToSignedOut).toHaveBeenCalled());
    expect(mock.callsTo("POST /api/session/logout")[0]?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
  });

  it("offers a retry when the console is down", async () => {
    let n = 0;
    mockFetch().on("GET /api/session", () =>
      ++n === 1 ? fail(503, "unavailable", "down") : reply(200, SESSION),
    );
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("signed in as person@example.org")).toBeInTheDocument();
  });
});

describe("safeReturnTo", () => {
  it("keeps console paths and refuses anything else", () => {
    expect(navigation.safeReturnTo("/leaders/eu-1/jobs?state=failed")).toBe(
      "/leaders/eu-1/jobs?state=failed",
    );
    expect(navigation.safeReturnTo("//evil.example")).toBe("/");
    expect(navigation.safeReturnTo("https://evil.example")).toBe("/");
    expect(navigation.safeReturnTo("/\\evil")).toBe("/");
    expect(navigation.safeReturnTo("/sign-in?x=1")).toBe("/");
    expect(navigation.safeReturnTo(null)).toBe("/");
    expect(navigation.signInUrl("/admin/grants")).toBe("/sign-in?return_to=%2Fadmin%2Fgrants");
  });

  it("refuses hostile values", () => {
    const hostile = [
      "",
      "evil.example",
      "javascript:alert(1)",
      "data:text/html,x",
      "\\\\evil.example",
      "/\\/evil.example",
      "/\t/evil.example",
      "/\n/evil.example",
      "/\r/evil.example",
      "\t//evil.example",
      " //evil.example",
      "/\u0000x",
      "/\u007fx",
      "/\u0085x",
      "///evil.example",
      "/api/session",
      "/auth/login",
      "/sign-in",
      "/" + "a".repeat(600),
    ];
    for (const value of hostile) expect(navigation.safeReturnTo(value)).toBe("/");
    expect(navigation.signInUrl("//evil.example")).toBe("/sign-in");
  });
});

describe("goToSignIn", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does a full-page load of /sign-in carrying only a local return_to", () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/leaders/eu-1/jobs", search: "?state=failed", assign });
    navigation.goToSignIn();
    expect(assign).toHaveBeenCalledWith("/sign-in?return_to=%2Fleaders%2Feu-1%2Fjobs%3Fstate%3Dfailed");
  });

  it("drops a hostile current location", () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "//evil.example", search: "", assign });
    navigation.goToSignIn();
    expect(assign).toHaveBeenCalledWith("/sign-in");
  });
});
