# Server-rendered web presentation

- `templates` contains Jinja2 pages and HTMX partials.
- `static` contains local theme CSS and pinned HTMX with its license.
- The sibling API package owns routing, runtime lifespan and scan submission.

There is no Node.js frontend build or separate client-side domain model.
Keep presentation consistent with shared knowledge/provenance services and the Clawpilot theme.
See `tests\integration\test_interfaces.py` for page and partial-response checks.
