# Browser assets

- `theme.css` uses [Bold Wikipedia](https://tweakcn.com/r/themes/cmlmpb3qp000004l5go47hzsv)
  light/dark shadcn tokens; `--cp-*` aliases keep custom views on the same palette.
- `ui` contains the built React/shadcn application, source fingerprints and bundled licenses.
- FastAPI mounts these local assets; runtime uses no CDN, Node.js server or third-party requests.
- The build packages the Inter variable font locally and retains its SIL Open Font License.

Use existing `--cp-*` color variables and keep changes local to presentation.
Do not edit generated assets; rebuild from `frontend` and commit them with source changes.
Python tests reject stale assets; integration tests check delivery and conditional caching.
