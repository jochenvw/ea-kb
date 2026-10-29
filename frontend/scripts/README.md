# Dependency maintenance

`use-feed.mjs` normalizes Microsoft mirror tarball URLs to the configured upstream.
The mirror's metadata can contain Azure Artifacts paths that npm cannot rewrite correctly.
After `npm install --package-lock-only ...`, run `npm run lock:feed` and `npm ci`.
Integrity hashes and package safety settings remain unchanged; unexpected registries fail closed.
