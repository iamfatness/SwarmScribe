// Light, dark, or the system's choice (the default). The choice is the only thing the app
// keeps in the browser; it is applied as <html data-theme="light|dark">, and styles.css
// follows prefers-color-scheme when the attribute is absent.

export type ThemeChoice = "system" | "light" | "dark";

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
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") storage()?.removeItem(THEME_KEY);
    else storage()?.setItem(THEME_KEY, choice);
  } catch {
    // Storage refused (private mode): the choice lasts for this page only.
  }
  applyTheme(choice);
}
