import { useCallback, useEffect, useState } from "react";

/**
 * useUrlParam — like `useState` but mirrored to a URL search-param so that
 * page-refresh, share-link and browser back/forward all restore the exact
 * sub-state (active tab, sort key, filter, page number ...).
 *
 *   const [style, setStyle] = useUrlParam("style", "all");
 *
 * - Missing param → returns `defaultValue`.
 * - Setting back to `defaultValue` removes the param (URL stays clean).
 * - Pushes via `history.replaceState` so it doesn't bloat the history stack.
 * - Listens to `popstate` so external back/forward keeps state in sync.
 */
export function useUrlParam<T extends string>(
  key: string,
  defaultValue: T,
): [T, (v: T) => void] {
  const read = useCallback((): T => {
    if (typeof window === "undefined") return defaultValue;
    const v = new URLSearchParams(window.location.search).get(key);
    return (v as T) ?? defaultValue;
  }, [key, defaultValue]);

  const [value, setValue] = useState<T>(read);

  // Keep state in sync when user navigates with browser back/forward
  useEffect(() => {
    const onPop = () => setValue(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [read]);

  const update = useCallback((v: T) => {
    setValue(v);
    const sp = new URLSearchParams(window.location.search);
    if (v === defaultValue || v === "" || v == null) sp.delete(key);
    else sp.set(key, String(v));
    const qs = sp.toString();
    const next = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
    if (next !== window.location.pathname + window.location.search + window.location.hash) {
      window.history.replaceState(null, "", next);
    }
  }, [key, defaultValue]);

  return [value, update];
}

/** Number variant. */
export function useUrlNumParam(
  key: string,
  defaultValue: number,
): [number, (v: number) => void] {
  const [s, setS] = useUrlParam(key, String(defaultValue));
  return [Number(s) || defaultValue, (v: number) => setS(String(v))];
}

/** Boolean variant: present (=1) means true. */
export function useUrlBoolParam(
  key: string,
  defaultValue = false,
): [boolean, (v: boolean) => void] {
  const [s, setS] = useUrlParam(key, defaultValue ? "1" : "0");
  return [s === "1", (v: boolean) => setS(v ? "1" : "0")];
}
