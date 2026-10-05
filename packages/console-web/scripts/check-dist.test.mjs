import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { checkDist } from "./check-dist.mjs";

// npm runs vitest from packages/console-web.
const SCRIPT = join(process.cwd(), "scripts", "check-dist.mjs");
const GOOD_HTML =
  '<!doctype html><html><head><script src="/assets/theme-0a1b2c3d4e5f6a7b.js"></script>' +
  '<link rel="stylesheet" href="/assets/style-f8b31e785625c3d8.css"></head>' +
  '<body><div id="root"></div><script type="module" src="/assets/index-86e44ad0b53f19b4.js"></script></body></html>';
const dirs = [];

function fixture({ html = GOOD_HTML, css = "body{color:red}", js = "export{}", theme = "(function(){})()", extra = {} } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "check-dist-"));
  dirs.push(dir);
  mkdirSync(join(dir, "assets"));
  if (html !== null) writeFileSync(join(dir, "index.html"), html);
  writeFileSync(join(dir, "assets", "style-f8b31e785625c3d8.css"), css);
  writeFileSync(join(dir, "assets", "index-86e44ad0b53f19b4.js"), js);
  writeFileSync(join(dir, "assets", "theme-0a1b2c3d4e5f6a7b.js"), theme);
  for (const [name, text] of Object.entries(extra)) writeFileSync(join(dir, "assets", name), text);
  return dir;
}

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const has = (dir, text) => checkDist(dir).problems.some((p) => p.includes(text));

describe("check-dist", () => {
  it("passes a clean build", () => {
    expect(checkDist(fixture()).problems).toEqual([]);
  });

  it("accepts a data:image url() in CSS", () => {
    expect(checkDist(fixture({ css: "a{background:url(data:image/png;base64,AAAA)}" })).problems).toEqual([]);
  });

  it.each([
    ["inline script", GOOD_HTML.replace("</body>", "<script>alert(1)</script></body>"), "inline <script>"],
    ["inline style", GOOD_HTML.replace("</head>", "<style>a{}</style></head>"), "inline <style>"],
    ["style attribute", GOOD_HTML.replace("<div", '<div style="color:red"'), "style attribute"],
    ["double-quoted off-origin", GOOD_HTML.replace("</head>", '<link href="https://x.test/a.css"></head>'), "off-origin"],
    ["single-quoted off-origin", GOOD_HTML.replace("</head>", "<link href='https://x.test/a.css'></head>"), "off-origin"],
    ["unquoted off-origin", GOOD_HTML.replace("</head>", "<link href=https://x.test/a.css></head>"), "off-origin"],
    ["protocol-relative", GOOD_HTML.replace("</head>", '<script src="//x.test/a.js"></script></head>'), "off-origin"],
  ])("refuses index.html with %s", (_name, html, text) => {
    expect(has(fixture({ html }), text)).toBe(true);
  });

  it.each([
    ["@import url()", "@import url(https://fonts.example/a.css);body{}"],
    ["@import quoted", '@import "https://fonts.example/a.css";'],
    ["@import protocol-relative", "@import url('//fonts.example/a.css');"],
    ["url() off-origin", "a{background:url(https://x.test/a.png)}"],
    ["url() quoted off-origin", '@font-face{src:url("//x.test/a.woff2")}'],
    ["data: font", "@font-face{src:url(data:font/woff2;base64,AAAA)}"],
    ["data: non-image", "a{background:url(data:text/html;base64,AAAA)}"],
  ])("refuses CSS with %s", (_name, css) => {
    expect(has(fixture({ css }), "style-f8b31e785625c3d8.css")).toBe(true);
  });

  const THEME_TAG = /<script src="\/assets\/theme[^>]*><\/script>/;

  it.each([
    ["is missing", GOOD_HTML.replace(THEME_TAG, ""), "no theme script"],
    ["is a module", GOOD_HTML.replace('<script src="/assets/theme', '<script type="module" src="/assets/theme'), "classic"],
    ["is deferred", GOOD_HTML.replace('<script src="/assets/theme', '<script defer src="/assets/theme'), "classic"],
    ["is async", GOOD_HTML.replace('<script src="/assets/theme', '<script async src="/assets/theme'), "classic"],
    [
      "is in the body",
      GOOD_HTML.replace(THEME_TAG, "").replace("</body>", '<script src="/assets/theme-0a1b2c3d4e5f6a7b.js"></script></body>'),
      "no theme script",
    ],
  ])("refuses a build whose theme script %s", (_name, html, text) => {
    expect(has(fixture({ html }), text)).toBe(true);
  });

  it("refuses a theme script that still has an import", () => {
    expect(has(fixture({ theme: 'import{a}from"./x.js";a()' }), "theme-0a1b2c3d4e5f6a7b.js")).toBe(true);
  });

  it("refuses an un-hashed asset", () => {
    expect(has(fixture({ extra: { "plain.js": "" } }), "plain.js")).toBe(true);
  });

  it("refuses test code in the bundle", () => {
    expect(has(fixture({ js: 'import "@testing-library/react"' }), "@testing-library")).toBe(true);
  });

  it("prints one clear line, no stack trace, when index.html is missing", () => {
    const dir = fixture({ html: null });
    const run = spawnSync(process.execPath, [SCRIPT, dir], { encoding: "utf8" });
    expect(run.status).toBe(1);
    expect(run.stderr.trim().split("\n")).toHaveLength(1);
    expect(run.stderr).toContain("index.html is missing");
    expect(run.stderr).not.toContain("    at ");
  });
});
