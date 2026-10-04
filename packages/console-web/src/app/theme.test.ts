/* eslint-disable no-restricted-globals -- the theme test inspects the one stored key */
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, readTheme, saveTheme } from "./theme";

describe("theme", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    applyTheme("system");
  });

  it("stores light and dark, and removes the key for system", () => {
    saveTheme("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(readTheme()).toBe("dark");
    saveTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(readTheme()).toBe("system");
  });

  it("ignores a stored value it does not know", () => {
    localStorage.setItem(THEME_KEY, "<script>");
    expect(readTheme()).toBe("system");
  });

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readTheme()).toBe("system");
    saveTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
