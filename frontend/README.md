# Browser application

React, TypeScript, Vite and actual shadcn/ui components; FastAPI remains the only production server.
Contributor builds require Node.js 22.12+; normal Python operation does not require Node.
From this folder: `npm ci`, `npm run build`, `npm run check`, `npm run format:check`, `npm test`.
`npm run dev` optionally proxies the local backend on port 8000 for UI development.
The Microsoft npm upstream is configured in `.npmrc`; dependency maintenance is in `scripts\README.md`.
Built assets are committed under `src\ekb\interfaces\web\static\ui` so Python users need no Node runtime.
Rebuild after source/configuration changes; the Python suite detects stale generated assets.
See `src\README.md` for presentation boundaries and `tests\README.md` for browser verification.
