import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The console serves dist/ (SWARMSCRIBE_CONSOLE_STATIC_DIR) under a CSP with no inline
// script or style, and caches for a year only names that look content-hashed: a "-" then
// 8+ letters/digits/underscores with at least one digit, then the extension. Hex hashes of
// 16 characters fit that shape; scripts/check-dist.mjs refuses a build where one does not.
export default defineConfig({
  plugins: [react()],
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
