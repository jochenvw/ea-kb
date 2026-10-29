# Durable pipeline coordination

`orchestrator.py` orders registered stages, validates compatible upstream artifacts and owns
renewable execution leases, cancellation and resume. It contains no knowledge algorithms.
`registry.py` validates explicit stage/check plugins; registering a plugin does not enable models.
Execution state and check caches use the unit-of-work ports and PostgreSQL adapter.
Optional progress observes committed executions; stage summaries retain workspace deltas/check reuse.
See `docs\phase3.md` and orchestration integration tests.
