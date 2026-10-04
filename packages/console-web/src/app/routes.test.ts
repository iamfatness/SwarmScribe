import { describe, expect, it } from "vitest";
import prefixes from "./routePrefixes.json";
import { isRouted } from "./routes";

describe("isRouted", () => {
  it("routes the root and every listed first segment", () => {
    expect(isRouted("/")).toBe(true);
    for (const prefix of prefixes) {
      expect(isRouted(prefix)).toBe(true);
      if (prefix !== "/") expect(isRouted(`${prefix}/x/y`)).toBe(true);
    }
  });

  it("does not route a first segment that is not listed", () => {
    expect(isRouted("/reports")).toBe(false);
    expect(isRouted("/leadersx")).toBe(false);
    expect(isRouted("/healthz")).toBe(false);
    expect(isRouted("//leaders")).toBe(false);
    expect(isRouted("")).toBe(false);
  });

  it("lists only well-formed prefixes", () => {
    for (const prefix of prefixes) expect(prefix).toMatch(/^\/([a-z0-9-]+)?$/);
  });
});
