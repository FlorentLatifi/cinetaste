import { useCallback, useEffect, useState } from "react";

export type ColorSchemePreference = "system" | "light" | "dark";
export type ResolvedColorScheme = "light" | "dark";

const STORAGE_KEY = "ct_color_scheme";
const CHANGE_EVENT = "ct-color-scheme-change";

export function getStoredColorScheme(): ColorSchemePreference | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "system" || raw === "light" || raw === "dark") return raw;
  } catch {
    // private mode / blocked storage
  }
  return null;
}

export function systemPrefersLight(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

export function resolveColorScheme(
  preference?: ColorSchemePreference | null,
): ResolvedColorScheme {
  const pref = preference ?? getStoredColorScheme() ?? "system";
  if (pref === "light") return "light";
  if (pref === "dark") return "dark";
  return systemPrefersLight() ? "light" : "dark";
}

export function applyColorScheme(resolved: ResolvedColorScheme): void {
  document.documentElement.setAttribute("data-theme", resolved);
  // Help native form controls / scrollbars match
  document.documentElement.style.colorScheme = resolved;
}

export function persistColorScheme(preference: ColorSchemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // ignore
  }
  applyColorScheme(resolveColorScheme(preference));
  window.dispatchEvent(
    new CustomEvent<ColorSchemePreference>(CHANGE_EVENT, { detail: preference }),
  );
}

/** Apply stored/system theme as early as possible (main bootstrap). */
export function initColorScheme(): void {
  applyColorScheme(resolveColorScheme());
}

export function useColorScheme() {
  const [preference, setPreferenceState] = useState<ColorSchemePreference>(
    () => getStoredColorScheme() ?? "system",
  );
  const [resolved, setResolved] = useState<ResolvedColorScheme>(() =>
    resolveColorScheme(getStoredColorScheme() ?? "system"),
  );

  useEffect(() => {
    applyColorScheme(resolved);
  }, [resolved]);

  useEffect(() => {
    const sync = () => {
      const pref = getStoredColorScheme() ?? "system";
      setPreferenceState(pref);
      setResolved(resolveColorScheme(pref));
    };
    const onAppChange = (e: Event) => {
      const detail = (e as CustomEvent<ColorSchemePreference>).detail;
      if (detail === "system" || detail === "light" || detail === "dark") {
        setPreferenceState(detail);
        setResolved(resolveColorScheme(detail));
      }
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) sync();
    };
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onMq = () => {
      const pref = getStoredColorScheme() ?? "system";
      if (pref === "system") setResolved(resolveColorScheme("system"));
    };

    window.addEventListener(CHANGE_EVENT, onAppChange);
    window.addEventListener("storage", onStorage);
    mq.addEventListener("change", onMq);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onAppChange);
      window.removeEventListener("storage", onStorage);
      mq.removeEventListener("change", onMq);
    };
  }, []);

  const setPreference = useCallback((next: ColorSchemePreference) => {
    persistColorScheme(next);
    setPreferenceState(next);
    setResolved(resolveColorScheme(next));
  }, []);

  return { preference, resolved, setPreference, isLight: resolved === "light" };
}
