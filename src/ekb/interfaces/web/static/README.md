# Browser assets

- `theme.css` defines the Clawpilot light/dark theme and component styling.
- `htmx.min.js` is the pinned third-party browser library; retain `HTMX-LICENSE.txt`.
- Assets are mounted by the FastAPI application; no external CDN or frontend build is required.

Use existing `--cp-*` color variables and keep changes local to presentation.
Do not hand-edit vendored minified code; update it deliberately with version/license checks.
Web integration tests check asset delivery.
