import react from "@vitejs/plugin-react";
import { build, type Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * The chosen theme must be applied before the first paint, and the app's module script
 * is deferred. This builds src/theme-boot.ts on its own as a classic (IIFE) script with
 * no imports, emits it as assets/theme-<hash>.js and puts a plain, render-blocking
 * <script src> in <head>: same origin, hashed, nothing inline, so the CSP holds.
 */
function themeBoot(): Plugin {
  let reference = "";
  return {
    name: "swarmscribe-theme-boot",
    apply: "build",
    enforce: "post",
    async buildStart() {
      const built = await build({
        configFile: false,
        logLevel: "silent",
        build: {
          write: false,
          minify: true,
          lib: {
            entry: "src/theme-boot.ts",
            formats: ["iife"],
            name: "swarmscribeThemeBoot",
          },
        },
      });
      const outputs = Array.isArray(built) ? built : [built];
      const chunk = outputs
        .flatMap((one) => ("output" in one ? one.output : []))
        .find((o) => o.type === "chunk");
      if (chunk === undefined || chunk.type !== "chunk")
        throw new Error("the theme script did not build");
      reference = this.emitFile({
        type: "asset",
        name: "theme.js",
        source: chunk.code,
      });
    },
    generateBundle(_options, bundle) {
      const page = bundle["index.html"];
      if (page === undefined || page.type !== "asset") return;
      const tag = `<script src="/${this.getFileName(reference)}"></script>`;
      page.source = String(page.source).replace(
        "<title>",
        `${tag}\n    <title>`,
      );
    },
  };
}

// The console serves dist/ (SWARMSCRIBE_CONSOLE_STATIC_DIR) under a CSP with no inline
// script or style, and caches for a year only names that look content-hashed: a "-" then
// 8+ letters/digits/underscores with at least one digit, then the extension. Hex hashes of
// 16 characters fit that shape; scripts/check-dist.mjs refuses a build where one does not.
export default defineConfig({
  plugins: [react(), themeBoot()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    modulePreload: { polyfill: false },
    sourcemap: false,
    rollupOptions: {
      output: {
        hashCharacters: "hex",
        entryFileNames: "assets/[name]-[hash:16].js",
        chunkFileNames: "assets/[name]-[hash:16].js",
        assetFileNames: "assets/[name]-[hash:16][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.mjs"],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
