# Browser assets

- `theme.css` defines the canonical Clawpilot light/dark tokens consumed by the frontend build.
- `ui` contains the built React/shadcn application, source fingerprints and bundled licenses.
- FastAPI mounts these local assets; runtime uses no CDN, Node.js server or third-party requests.

Use existing `--cp-*` color variables and keep changes local to presentation.
Do not edit generated assets; rebuild from `frontend` and commit them with source changes.
Python tests reject stale assets; integration tests check delivery and conditional caching.
