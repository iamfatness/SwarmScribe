// Dark (the default, for everyone, whatever the system is set to), light, or the system's
// choice. Light and System are a person's own choices in the Theme switch; a choice is the
// only thing the app keeps in the browser. It is applied as
// <html data-theme="dark|light|system">: the stylesheet is dark with no attribute at all (so
// the first paint is dark even before the boot script runs), and follows
// prefers-color-scheme only under data-theme="system".

export type ThemeChoice = "dark" | "light" | "system";

export const DEFAULT_THEME: ThemeChoice = "dark";

export const THEME_KEY = "swarmscribe-console-theme";

function storage(): Storage | null {
  try {
    // eslint-disable-next-line no-restricted-globals -- the theme is the one stored preference
    return localStorage;
  } catch {
    return null;
  }
}

export function readTheme(): ThemeChoice {
  try {
    const value = storage()?.getItem(THEME_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.dataset.theme = choice;
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    storage()?.setItem(THEME_KEY, choice);
  } catch {
    // Storage refused (private mode): the choice lasts for this page only.
  }
  applyTheme(choice);
}
