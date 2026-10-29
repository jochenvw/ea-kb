import { readFileSync, writeFileSync } from "node:fs";

const file = new URL("../package-lock.json", import.meta.url);
const lock = JSON.parse(readFileSync(file, "utf8"));
const feed = "https://packagefeedproxy.microsoft.io/npm/";

for (const entry of Object.values(lock.packages)) {
  if (!entry.resolved) continue;
  const url = new URL(entry.resolved);
  if (
    url.hostname !== "packagefeedproxy.microsoft.io" &&
    !/^ms-feed-\d+\.pkgs\.visualstudio\.com$/.test(url.hostname)
  ) {
    throw new Error(`Unexpected dependency registry: ${url.hostname}`);
  }
  const path = url.pathname.includes("/npm/registry/")
    ? url.pathname.split("/npm/registry/")[1]
    : url.pathname.replace(/^\/npm\//, "");
  entry.resolved = feed + path;
}

writeFileSync(file, JSON.stringify(lock, null, 2) + "\n");
