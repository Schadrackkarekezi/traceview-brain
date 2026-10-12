import { createContext, useContext, useEffect, useState } from "react";
import { readString } from "./storage";

const STORAGE_KEY = "traceview_theme";
const ThemeContext = createContext(null);

// "system" means no explicit choice - CSS's own @media (prefers-color-scheme)
// handles it, nothing to set here. "light"/"dark" stamp data-theme on <html>,
// which is what the :root[data-theme="..."] blocks in index.css key off of.
function applyTheme(theme) {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => readString(localStorage, STORAGE_KEY, "system"));
  // The actual light/dark value in effect right now - "system" isn't a
  // real theme, it's "whatever the OS says," so anything needing a
  // concrete choice (CodeMirror's theme prop takes "light"/"dark", not
  // "system") reads this instead of `theme` directly.
  const [resolvedTheme, setResolvedTheme] = useState(() =>
    theme === "system" ? (systemPrefersDark() ? "dark" : "light") : theme
  );

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") {
      setResolvedTheme(theme);
      return;
    }
    setResolvedTheme(systemPrefersDark() ? "dark" : "light");

    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolvedTheme(mql.matches ? "dark" : "light");
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = (next) => {
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage unavailable (private mode, etc.) - theme just won't
      // persist across reloads, not worth failing over.
    }
  };

  return (
    <ThemeContext.Provider value={[theme, setTheme, resolvedTheme]}>
      {children}
    </ThemeContext.Provider>
  );
}

// One shared theme state via context, not independent useState per call -
// otherwise toggling the theme in the sidebar wouldn't be seen by anything
// else reading it (e.g. the SQL editor's CodeMirror theme prop) until that
// component happened to re-render for an unrelated reason.
export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}
