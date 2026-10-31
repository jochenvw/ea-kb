# HTTP and web application

`create_app` in `__init__.py` owns FastAPI routes and application/runtime lifespan.

- `/api` endpoints inspect persisted sources, concepts, provenance, lineage and governance.
- Browser routes, including `/company`, `/architecture` and `/platforms`, return the prebuilt
  React/shadcn HTML file without templates; `/health` checks PostgreSQL.
- `/ontology` and `/api/ontology` expose the read-only packaged OWL catalog without database/model I/O.
- `/api/ui/config` exposes whether governance is enabled, never credentials.
- `/api/concepts` accepts `after`, `source_id`, `level` and repeated `semantic_type` filters;
  constraints select the same version.
- `/api/sources` lists individual original documents for exact-source inspection and provenance.
- `inspection.py` serves source drill-down, graphs, outstanding proposals and read-only table/log pages.
- Inspection permits EKB-owned tables only, not arbitrary SQL, files, raw traces or mutations.
- HTTP routes neither start nor monitor ingestion; use the CLI for pipeline progress and status.
- `governance.py` exposes strict resolution, issues, audit and `/review` through shared services.
- Mutations require configured Basic token authentication, same origin and HTTPS or loopback.
- All non-health web and API routes reject non-loopback clients and forwarded-proxy headers.
- Enterprise identity and remote access are not implemented; do not expose this service through a proxy.

The web lifespan owns no ingestion; use CLI pipeline/status/resume and the worker.
Origin checks are not enterprise authentication; keep this service local-only.
See `tests\unit\test_api_access.py` and
`tests\integration\test_interfaces.py`.
