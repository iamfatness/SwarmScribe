/* eslint-disable no-restricted-globals -- the theme test inspects the one stored key */
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THEME, THEME_KEY, applyTheme, readTheme, saveTheme } from "./theme";

describe("theme", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  it("is dark for everyone until a person chooses otherwise", () => {
    expect(DEFAULT_THEME).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(readTheme()).toBe("dark");
    applyTheme(readTheme());
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("stores each of the three choices, System among them, and reads it back", () => {
    saveTheme("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(readTheme()).toBe("light");
    saveTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("system");
    expect(readTheme()).toBe("system");
    saveTheme("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(readTheme()).toBe("dark");
  });

  it("ignores a stored value it does not know", () => {
    localStorage.setItem(THEME_KEY, "<script>");
    expect(readTheme()).toBe("dark");
  });

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readTheme()).toBe("dark");
    saveTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("follows the system's setting in the stylesheet only where System was chosen", () => {
    const css = readFileSync("src/styles/tokens.css", "utf8");
    const block = css.slice(css.indexOf("@media (prefers-color-scheme: light)"));
    const selectors = block.match(/^ {2}:root[^{]*/gm) ?? [];
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) expect(selector).toContain(':root[data-theme="system"]');
    // No rule anywhere is light merely because the attribute is missing.
    expect(css).not.toContain(":root:not([data-theme");
  });
});
