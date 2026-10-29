# HTTP and web application

`create_app` in `__init__.py` owns FastAPI routes and application/runtime lifespan.

- `/api` endpoints inspect persisted sources, scans, concepts, provenance, lineage and usage.
- `/` and concept pages render the sibling `web` templates; `/health` checks PostgreSQL.
- `/scans` starts one application-owned task using server-configured sources, not arbitrary paths.
- `/api/benchmarks` exposes editions, controls, comparisons and evidence chains; `/benchmarks` graphs NS-6.
- Benchmark inspection is read-only: cached results or structural previews, never model calls.

Keep handlers thin and cancel owned work on shutdown. Origin checks are not enterprise authentication;
keep this service local/private. See `tests\integration\test_interfaces.py`.
