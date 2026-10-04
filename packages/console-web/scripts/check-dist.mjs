// Refuses a build the console would serve wrongly. Run after `vite build` (npm run build).
// - index.html: no inline <script> or <style>, no style="" attribute, nothing off-origin
//   (any quoting of the attribute value).
// - built CSS: no off-origin @import or url(); data: only for images (the CSP's img-src
//   allows data:; font-src and style-src do not).
// - every file under dist/assets has a name the console caches as content-hashed
//   (packages/console/src/swarmscribe_console/static.py, _HASHED).
// - nothing from the end-to-end harness or a test reached the bundle.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HASHED = /[.-](?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+/;
const FORBIDDEN_IN_BUNDLE = ["/control/", "e2e/harness", "fake_leaders", "@testing-library"];
// A URL with a scheme (https:, data:, javascript:...) or protocol-relative (//host).
const OFF_ORIGIN = /^\s*(?:[a-z][a-z0-9+.-]*:|\/\/)/i;
const URL_ATTRIBUTE =
  /\b(?:src|href|action|poster|data|srcset|formaction)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))/gi;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function checkHtml(html, problems) {
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const [, attributes, body] = match;
    if (!/\bsrc=/.test(attributes) || body.trim() !== "") problems.push("index.html has an inline <script>");
  }
  if (/<style\b/i.test(html)) problems.push("index.html has an inline <style>");
  if (/\sstyle\s*=/i.test(html)) problems.push("index.html has a style attribute");
  for (const match of html.matchAll(URL_ATTRIBUTE)) {
    const value = match[1] ?? match[2] ?? match[3] ?? "";
    // data:image/... and the empty data:, icon are allowed by the CSP's img-src.
    if (/^\s*data:(?:,|image\/)/i.test(value)) continue;
    if (OFF_ORIGIN.test(value)) problems.push("index.html loads something off-origin");
  }
}

function checkCss(name, css, problems) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const match of text.matchAll(/@import\s+(?:url\(\s*)?(?:"([^"]*)"|'([^']*)'|([^\s;)]+))/gi)) {
    const target = match[1] ?? match[2] ?? match[3] ?? "";
    if (OFF_ORIGIN.test(target)) problems.push(`${name} @imports an off-origin or data: URL`);
  }
  for (const match of text.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)/gi)) {
    const target = match[1] ?? match[2] ?? match[3] ?? "";
    if (/^\s*data:image\//i.test(target)) continue;
    if (OFF_ORIGIN.test(target)) {
      problems.push(`${name} has a url() that is off-origin or a non-image data: URL`);
    }
  }
}

/** The problems with a built dist directory (none when it is servable). */
export function checkDist(dist) {
  const problems = [];
  const htmlPath = join(dist, "index.html");
  if (!existsSync(htmlPath)) return { missing: true, problems: ["index.html is missing"], assets: 0 };
  checkHtml(readFileSync(htmlPath, "utf8"), problems);

  const assetsDir = join(dist, "assets");
  const assets = existsSync(assetsDir) ? walk(assetsDir) : [];
  if (assets.length === 0) problems.push("dist/assets is empty");
  for (const path of assets) {
    const name = basename(path);
    if (!HASHED.test(name)) problems.push(`${name} is not named like a content-hashed file`);
    if (/\.(js|css)$/.test(name)) {
      const text = readFileSync(path, "utf8");
      for (const needle of FORBIDDEN_IN_BUNDLE) {
        if (text.includes(needle)) problems.push(`${name} contains ${JSON.stringify(needle)}`);
      }
      if (name.endsWith(".css")) checkCss(name, text, problems);
    }
  }
  return { missing: false, problems, assets: assets.length };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const dist = process.argv[2] ?? fileURLToPath(new URL("../dist/", import.meta.url));
  const result = checkDist(dist);
  if (result.missing) {
    console.error("dist/ is not servable by the console: index.html is missing (run vite build first)");
    process.exit(1);
  }
  if (result.problems.length > 0) {
    console.error("dist/ is not servable by the console:\n- " + result.problems.join("\n- "));
    process.exit(1);
  }
  console.log(`dist/ ok: index.html and ${result.assets} hashed assets`);
}
