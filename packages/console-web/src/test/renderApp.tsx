import { render } from "@testing-library/react";
import { App } from "../App";
import type { FleetLeader, SessionInfo } from "../api/types";
import { mockFetch, reply, type FetchMock } from "./fetchMock";
import { SESSION, leader } from "./fixtures";

/**
 * The whole app at `path`, signed in, with GET /api/session and GET /api/fleet answered.
 * Returns the fetch mock so a test can add the routes it needs.
 */
export function renderApp(
  path: string,
  { fleet = [leader()], session = SESSION }: { fleet?: FleetLeader[]; session?: SessionInfo } = {},
): FetchMock {
  window.history.replaceState(null, "", path);
  const mock = mockFetch()
    .on("GET /api/session", reply(200, session))
    .on("GET /api/fleet", reply(200, fleet));
  render(<App />);
  return mock;
}
