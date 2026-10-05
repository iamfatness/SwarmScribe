import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// The colour tokens are the contrast contract (spec section 3 and 7): this test measures
// them, so a token cannot drift below WCAG AA without a failing test. npm runs vitest from
// packages/console-web.
const CSS = readFileSync(join(process.cwd(), "src", "styles", "tokens.css"), "utf8");

type Tokens = Record<string, string>;

/** Every block that follows a "token-set: <name>" comment, as { token: value }. */
function sets(name: string): Tokens[] {
  const found: Tokens[] = [];
  const marker = new RegExp(`/\\* token-set: ${name} \\*/[^{]*\\{([^}]*)\\}`, "g");
  for (const match of CSS.matchAll(marker)) {
    const tokens: Tokens = {};
    for (const line of (match[1] ?? "").matchAll(/(--[a-z0-9-]+|color-scheme):\s*([^;]+);/g)) {
      tokens[line[1] as string] = (line[2] as string).trim();
    }
    found.push(tokens);
  }
  return found;
}

function luminance(hex: string): number {
  const value = /^#([0-9a-f]{6})$/i.exec(hex)?.[1];
  if (value === undefined) throw new Error(`${hex} is not a six-digit hex colour`);
  const [r, g, b] = [0, 2, 4].map((at) => {
    const channel = parseInt(value.slice(at, at + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

// [foreground, background]: body-size text, so 4.5:1.
const TEXT_PAIRS: [string, string][] = [
  ["--text", "--ground"],
  ["--text", "--panel"],
  ["--text", "--panel-2"],
  ["--text", "--accent-wash"],
  ["--text", "--bad-wash"],
  ["--text", "--field-bg"],
  ["--muted", "--ground"],
  ["--muted", "--panel"],
  ["--muted", "--panel-2"],
  ["--muted", "--accent-wash"],
  ["--accent-text", "--ground"],
  ["--accent-text", "--panel"],
  ["--accent-text", "--panel-2"],
  ["--accent-hover", "--ground"],
  ["--accent-hover", "--panel"],
  ["--bad", "--ground"],
  ["--bad", "--panel"],
  ["--bad", "--panel-2"],
  ["--bad", "--bad-wash"],
  ["--primary-fg", "--primary-bg"],
];

// Marks, chart lines, focus rings and the edges that make a field or a switched-off button
// findable: 3:1 (WCAG 1.4.11).
const MARK_PAIRS: [string, string][] = [
  ["--accent", "--ground"],
  ["--accent", "--panel"],
  ["--accent", "--accent-wash"],
  ["--focus", "--ground"],
  ["--focus", "--panel"],
  ["--edge", "--ground"],
  ["--edge", "--panel"],
  ["--edge", "--field-bg"],
  ["--bad", "--panel"],
  ["--primary-bg", "--ground"],
  ["--primary-bg", "--panel"],
];

describe("design tokens", () => {
  it.each(["ink", "paper"])("writes the %s set the same way everywhere it appears", (name) => {
    const copies = sets(name);
    expect(copies).toHaveLength(2);
    expect(copies[1]).toEqual(copies[0]);
    expect(Object.keys(copies[0] ?? {}).length).toBeGreaterThan(15);
  });

  it("gives both sets the same token names", () => {
    const [ink] = sets("ink");
    const [paper] = sets("paper");
    expect(Object.keys(paper ?? {}).sort()).toEqual(Object.keys(ink ?? {}).sort());
  });

  it("makes ink dark and paper light", () => {
    expect(sets("ink")[0]?.["color-scheme"]).toBe("dark");
    expect(sets("paper")[0]?.["color-scheme"]).toBe("light");
  });

  for (const name of ["ink", "paper"]) {
    const tokens = sets(name)[0] ?? {};
    it.each(TEXT_PAIRS)(`${name}: %s on %s is at least 4.5:1`, (fg, bg) => {
      expect(contrast(tokens[fg] as string, tokens[bg] as string)).toBeGreaterThanOrEqual(4.5);
    });
    it.each(MARK_PAIRS)(`${name}: %s against %s is at least 3:1`, (fg, bg) => {
      expect(contrast(tokens[fg] as string, tokens[bg] as string)).toBeGreaterThanOrEqual(3);
    });
  }

  it("keeps the brand constants", () => {
    expect(CSS).toMatch(/--amber:\s*#f5b83d;/);
    const [ink] = sets("ink");
    const [paper] = sets("paper");
    expect(ink?.["--ground"]).toBe("#14110d");
    expect(ink?.["--text"]).toBe("#f3ead9");
    expect(paper?.["--ground"]).toBe("#f3ead9");
    // The skip link and the logo are amber with ink text in both themes.
    expect(contrast("#14110d", "#f5b83d")).toBeGreaterThanOrEqual(4.5);
  });

  it("loads no font and no image from anywhere", () => {
    expect(CSS).not.toMatch(/url\(|@font-face|@import/);
  });
  it("writes no colour anywhere but this file (the logo's two ink constants excepted)", () => {
    // A hex colour or an rgb()/hsl() function in any stylesheet or source file, comments aside.
    const COLOUR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/;
    const files = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) return files(path);
        return /\.(css|tsx?)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
      });
    const found: string[] = [];
    for (const file of files(join(process.cwd(), "src"))) {
      if (file.endsWith("tokens.css")) continue;
      readFileSync(file, "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, " "))
        .split("\n")
        .forEach((line, i) => {
          if (/^\s*\/\//.test(line)) return;
          if (COLOUR.test(line)) found.push(`${file.slice(process.cwd().length + 1)}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(found.map((entry) => entry.replace(/^.*?:\d+: /, "")).sort()).toEqual(["fill: #14110d;", "stroke: #14110d;"]);
    expect(found.every((entry) => entry.includes("shell.css"))).toBe(true);
  });
});
