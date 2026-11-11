// Light/dark theme, persisted per viewer (SPEC §12.4). The data-theme attribute
// on <html> drives the CSS variables in styles.css; "system" follows the OS.
export type Theme = "light" | "dark" | "system";

const KEY = "adjudik-theme";

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* storage may be unavailable */
  }
  return "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* ignore */
  }
}
