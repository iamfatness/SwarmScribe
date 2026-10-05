import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { countOf, formatDuration, formatTries, withUnit } from "./format";

const NBSP = " ";

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("a figure and its unit stay together", () => {
  it("withUnit joins them with a no-break space", () => {
    expect(withUnit(15, "min")).toBe(`15${NBSP}min`);
  });

  it("every formatter that puts a unit after a figure uses it", () => {
    expect(formatDuration(45)).toBe(`45${NBSP}s`);
    expect(formatDuration(15 * 60)).toBe(`15${NBSP}min`);
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe(`3${NBSP}h 5${NBSP}min`);
    expect(formatDuration(2 * 86400 + 4 * 3600)).toBe(`2${NBSP}d 4${NBSP}h`);
    expect(countOf(2, "follower")).toBe(`2${NBSP}followers`);
    expect(formatTries(3)).toBe(`three${NBSP}tries`);
  });

  it("no source file writes a figure, a plain space and a unit of time", () => {
    // "10 s", "15 min", "{n} h": code and JSX text only; comments are skipped. Use withUnit.
    const found: string[] = [];
    for (const file of sources(join(process.cwd(), "src"))) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
          if (/[0-9}] (?:s|min|h|d|ms)\b(?![-=(])/.test(line)) found.push(`${file}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(found).toEqual([]);
  });
});
