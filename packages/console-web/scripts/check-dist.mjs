// Refuses a build the console would serve wrongly. Run after `vite build` (npm run build).
// - index.html: no inline <script> or <style>, no style="" attribute, nothing off-origin.
// - every file under dist/assets has a name the console caches as content-hashed
//   (packages/console/src/swarmscribe_console/static.py, _HASHED).
// - nothing from the end-to-end harness or a test reached the bundle.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const HASHED = /[.-](?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+/;
const FORBIDDEN_IN_BUNDLE = ["/control/", "e2e/harness", "fake_leaders", "@testing-library"];
const problems = [];

const html = readFileSync(join(DIST, "index.html"), "utf8");
for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  const [, attributes, body] = match;
  if (!/\bsrc=/.test(attributes) || body.trim() !== "") problems.push("index.html has an inline <script>");
}
if (/<style\b/i.test(html)) problems.push("index.html has an inline <style>");
if (/\sstyle\s*=/i.test(html)) problems.push("index.html has a style attribute");
if (/(src|href)\s*=\s*"(https?:)?\/\//i.test(html)) problems.push("index.html loads something off-origin");

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}
const assets = walk(join(DIST, "assets"));
if (assets.length === 0) problems.push("dist/assets is empty");
for (const path of assets) {
  const name = basename(path);
  if (!HASHED.test(name)) problems.push(`${name} is not named like a content-hashed file`);
  if (/\.(js|css)$/.test(name)) {
    const text = readFileSync(path, "utf8");
    for (const needle of FORBIDDEN_IN_BUNDLE) {
      if (text.includes(needle)) problems.push(`${name} contains ${JSON.stringify(needle)}`);
    }
  }
}

if (problems.length > 0) {
  console.error("dist/ is not servable by the console:\n- " + problems.join("\n- "));
  process.exit(1);
}
console.log(`dist/ ok: index.html and ${assets.length} hashed assets`);
