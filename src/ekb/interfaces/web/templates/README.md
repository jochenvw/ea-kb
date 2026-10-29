# Jinja2 pages and HTMX partials

- `base.html` supplies the common document and theme setup.
- `index.html` composes the main page; `inventory.html` is the refreshable partial.
- `concept.html` shows interpretations, exact evidence, provenance and relationships.
- `benchmarks.html` visualizes NS-6 published mappings, proposed lineage, guidance and actual policies.
- Publication maturity is displayed separately from scoped governance/canonical decisions.
- `review.html` presents exact proposal evidence and authenticated, rationale-bearing review forms.

Route context is built by `src\ekb\interfaces\api\__init__.py`.
Keep source text escaped and business logic in services; partial responses must not duplicate
the whole HTML document. See `tests\integration\test_interfaces.py`.
