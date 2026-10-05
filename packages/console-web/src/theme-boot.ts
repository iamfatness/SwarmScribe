// Runs before the first paint: a classic script in <head> (vite.config.ts builds it on its
// own, with no imports left, and gives it a hashed name). It does only what main.tsx does
// once the app has loaded, through the same module, so the chosen theme is what is painted
// and not the system's for a moment.
import { applyTheme, readTheme } from "./app/theme";

applyTheme(readTheme());
