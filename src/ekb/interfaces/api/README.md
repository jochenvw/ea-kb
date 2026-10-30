# HTTP and web application

`create_app` in `__init__.py` owns FastAPI routes and application/runtime lifespan.

- `/api` endpoints inspect persisted sources, scans, concepts, provenance, lineage and usage.
- Browser routes serve the prebuilt React/shadcn shell; `/health` checks PostgreSQL.
- `/ontology` and `/api/ontology` expose the read-only packaged OWL catalog without database/model I/O.
- `/api/ui/config` exposes only governance-enabled and scan-running booleans, never credentials.
- `/api/concepts` accepts `after`, `source_id` and `level`; constraints select the same version.
- `/api/source-groups` consolidates benchmark editions; source drill-down/search accept group or document IDs.
- `/api/sources` remains the original document inventory for compatibility and exact provenance.
- `inspection.py` serves source drill-down, graphs, outstanding proposals and read-only table/log pages.
- Inspection permits EKB-owned tables only, not arbitrary SQL, files, raw traces or mutations.
- `/scans` starts one application-owned task using server-configured sources, not arbitrary paths.
- `/api/benchmarks` exposes editions, controls, comparisons and evidence chains.
- Benchmark inspection is read-only: cached results or structural previews, never model calls.
- `governance.py` exposes strict resolution, issues, audit and `/review` through shared services.
- Mutations require configured Basic token authentication, same origin and HTTPS or loopback.
- All non-health web and API routes reject non-loopback clients and forwarded-proxy headers.
- Enterprise identity and remote access are not implemented; do not expose this service through a proxy.

Keep handlers thin and cancel owned work on shutdown. Origin checks are not enterprise authentication;
keep this service local-only. See `tests\unit\test_api_access.py` and
`tests\integration\test_interfaces.py`.
