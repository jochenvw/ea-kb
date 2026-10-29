import { useEffect, useState } from "react";
import type { SemanticVersion } from "@/types";

export function useResource<T>(path: string | null) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    path: string | null;
    data: T | null;
    error: string | null;
    loading: boolean;
  }>({ path, data: null, error: null, loading: path !== null });

  useEffect(() => {
    const controller = new AbortController();
    setState({ path, data: null, error: null, loading: path !== null });
    if (path) {
      fetch(path, { signal: controller.signal, credentials: "same-origin" })
        .then(async (response) => {
          if (!response.ok)
            throw new Error(`Request failed (${response.status}).`);
          return (await response.json()) as T;
        })
        .then((data) => {
          if (!controller.signal.aborted) {
            setState({ path, data, error: null, loading: false });
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState({
              path,
              data: null,
              error:
                error instanceof Error
                  ? error.message
                  : "Could not load this view.",
              loading: false,
            });
          }
        });
    }
    return () => controller.abort();
  }, [path, revision]);

  return {
    ...(state.path === path
      ? state
      : { path, data: null, error: null, loading: path !== null }),
    reload: () => setRevision((value) => value + 1),
  };
}

export function useDebounced(value: string) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), 250);
    return () => window.clearTimeout(timer);
  }, [value]);
  return debounced;
}

export function label(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function date(value: string | null) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "Not recorded";
}

export function externalUrl(value: string) {
  return /^https?:\/\//i.test(value) ? value : undefined;
}

export function semanticVersion(value: SemanticVersion | null | undefined) {
  if (!value) return "Unknown";
  return `${value.major}.${value.minor}.${value.patch}${value.prerelease.length ? `-${value.prerelease.join(".")}` : ""}${value.build.length ? `+${value.build.join(".")}` : ""}`;
}
