import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ACTION_ROLE, atLeast, can } from "./roles";

// npm runs vitest from packages/console-web.
const PROXY_PY = resolve(process.cwd(), "../console/src/swarmscribe_console/proxy.py");

describe("roles", () => {
  it("matches the console's proxy allow-list exactly", () => {
    const source = readFileSync(PROXY_PY, "utf8");
    const routes = [
      ...source.matchAll(
        /ProxyRoute\(\s*"(?:GET|POST|PUT|PATCH|DELETE)",\s*"[^"]+",\s*"(viewer|operator|admin)",\s*"([a-z0-9_.]+)"/g,
      ),
    ];
    // Every ProxyRoute( construction must have been matched, so none is silently skipped.
    const constructed = [...source.matchAll(/^\s+ProxyRoute\(/gm)].length;
    expect(routes.length).toBeGreaterThan(0);
    expect(routes).toHaveLength(constructed);
    const fromPython = Object.fromEntries(routes.map(([, role, action]) => [action, role]));
    expect(ACTION_ROLE).toEqual(fromPython);
  });

  it("orders roles viewer < operator < admin", () => {
    expect(atLeast("admin", "operator")).toBe(true);
    expect(atLeast("operator", "admin")).toBe(false);
    expect(atLeast("viewer", "viewer")).toBe(true);
  });

  it("answers per action", () => {
    expect(can("operator", "jobs.retry")).toBe(true);
    expect(can("operator", "tokens.create")).toBe(false);
    expect(can("viewer", "consent.view")).toBe(true);
  });
});
