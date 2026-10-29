import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const directory = dirname(fileURLToPath(import.meta.url));

function packageArtifacts(): Plugin {
  return {
    name: "ekb-package-artifacts",
    generateBundle() {
      const inputs = [
        ...readdirSync(directory)
          .filter((name) => /\.(ts|js|json|html)$/.test(name))
          .map((name) => resolve(directory, name)),
        ...walk(resolve(directory, "src")).filter((name) =>
          /\.(tsx?|css)$/.test(name),
        ),
        resolve(directory, "../src/ekb/interfaces/web/static/theme.css"),
      ].sort();
      const hashes = Object.fromEntries(
        inputs.map((path) => [
          relative(resolve(directory, ".."), path).replaceAll("\\", "/"),
          createHash("sha256")
            .update(readFileSync(path, "utf8").replaceAll("\r\n", "\n"))
            .digest("hex"),
        ]),
      );
      this.emitFile({
        type: "asset",
        fileName: "sources.json",
        source: JSON.stringify(hashes, null, 2) + "\n",
      });
      this.emitFile({
        type: "asset",
        fileName: "README.md",
        source:
          "# Generated browser application\n\nBuilt from `frontend` with React and official shadcn/ui components.\nRun `npm ci` and `npm run build` in `frontend`; do not edit these assets.\nTracked assets let Python packages and containers run without Node.js.\n`sources.json` detects stale builds; `LICENSES.txt` retains third-party notices.\n",
      });
      const lock = JSON.parse(
        readFileSync(resolve(directory, "package-lock.json"), "utf8"),
      );
      const licenses = Object.entries(lock.packages)
        .filter(([name, value]) => name && !(value as { dev?: boolean }).dev)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([name]) => {
          const path = resolve(directory, name);
          const license = readdirSync(path).find((file) =>
            /^license(?:\.|$)/i.test(file),
          );
          const metadata = JSON.parse(
            readFileSync(resolve(path, "package.json"), "utf8"),
          );
          return `${metadata.name} ${metadata.version}\n${license ? readFileSync(resolve(path, license), "utf8") : `License: ${metadata.license}`}`;
        });
      this.emitFile({
        type: "asset",
        fileName: "LICENSES.txt",
        source:
          readFileSync(
            resolve(directory, "src/components/ui/LICENSE.md"),
            "utf8",
          ) +
          "\n\n" +
          licenses.join("\n\n"),
      });
    },
  };
}

function walk(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/ui/" : "/",
  plugins: [react(), packageArtifacts()],
  resolve: { alias: { "@": resolve(directory, "src") } },
  server: {
    proxy: Object.fromEntries(
      [
        "/api",
        "/scans",
        "/docs",
        "/openapi.json",
        "/health",
        "^/review/(issues|decisions|canonical)$",
      ].map((path) => [
        path,
        { target: "http://127.0.0.1:8000", changeOrigin: false },
      ]),
    ),
  },
  build: {
    outDir: "../src/ekb/interfaces/web/static/ui",
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: "app.js",
        assetFileNames: (asset) =>
          asset.names.some((name) => /\.woff2?$/.test(name))
            ? "[name]-[hash][extname]"
            : "app.[ext]",
      },
    },
  },
}));
