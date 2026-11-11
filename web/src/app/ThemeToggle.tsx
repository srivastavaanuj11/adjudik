import { useEffect, useState } from "react";
import { applyTheme, getStoredTheme, type Theme } from "./theme";

const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <button
      type="button"
      aria-label={`Theme: ${LABEL[theme]}. Click to change.`}
      title="Toggle theme"
      onClick={() => setTheme(ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length])}
    >
      {LABEL[theme]}
    </button>
  );
}
