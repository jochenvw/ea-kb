import { useEffect, useState } from "react";
import type { SemanticVersion, Version } from "@/types";

export function assertionMatches(
  version: Version | undefined,
  members: string[],
) {
  return (
    !members.length || members.includes(version?.assertion?.source_id ?? "")
  );
}

export function levelLabel(level: string | undefined | null) {
  return level === "implementation"
    ? "Platform implementation"
    : level === "top_level"
      ? "Goals & guidance"
      : "Level not classified";
}

export function goalLevelLabel(level: string | undefined | null) {
  if (level === "enterprise") return "Enterprise goal";
  if (level === "business_unit") return "Business-unit goal";
  if (level === "subordinate_target") return "Supporting target";
  return "Goal level unresolved";
}

export function assertionText(
  version: Version | undefined,
  sourceId?: string | string[] | null,
) {
  return (
    (sourceId &&
    !assertionMatches(version, Array.isArray(sourceId) ? sourceId : [sourceId])
      ? null
      : version?.assertion?.text) ??
    version?.candidate.statement.replace(/\s+/g, " ").trim() ??
    "No interpretation recorded"
  );
}

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
    .replace(/([a-z])([A-Z])/g, "$1 $2")
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
