import { useCallback, useEffect, useState } from "react";

export type ContrastMode = "normal" | "high";

const STORAGE_KEY = "ct_contrast";
const CHANGE_EVENT = "ct-contrast-change";

export function getStoredContrast(): ContrastMode | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "high" || raw === "normal") return raw;
  } catch {
    // private mode / blocked storage
  }
  return null;
}

export function systemPrefersHighContrast(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-contrast: more)").matches;
}

/** Explicit choice wins; otherwise follow OS when available. */
export function resolveContrast(): ContrastMode {
  const stored = getStoredContrast();
  if (stored) return stored;
  return systemPrefersHighContrast() ? "high" : "normal";
}

export function applyContrast(mode: ContrastMode): void {
  document.documentElement.setAttribute("data-contrast", mode);
}

export function persistContrast(mode: ContrastMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // ignore
  }
  applyContrast(mode);
  window.dispatchEvent(new CustomEvent<ContrastMode>(CHANGE_EVENT, { detail: mode }));
}

export function useContrast() {
  const [mode, setMode] = useState<ContrastMode>(() => resolveContrast());

  useEffect(() => {
    applyContrast(mode);
  }, [mode]);

  useEffect(() => {
    const onAppChange = (e: Event) => {
      const detail = (e as CustomEvent<ContrastMode>).detail;
      if (detail === "high" || detail === "normal") setMode(detail);
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      setMode(resolveContrast());
    };
    const mq = window.matchMedia("(prefers-contrast: more)");
    const onMq = () => {
      if (!getStoredContrast()) {
        setMode(systemPrefersHighContrast() ? "high" : "normal");
      }
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

  const setContrast = useCallback((next: ContrastMode) => {
    persistContrast(next);
    setMode(next);
  }, []);

  const toggle = useCallback(() => {
    setContrast(mode === "high" ? "normal" : "high");
  }, [mode, setContrast]);

  return { mode, setContrast, toggle, isHigh: mode === "high" };
}
