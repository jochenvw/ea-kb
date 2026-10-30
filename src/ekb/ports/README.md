# Interchangeable application contracts

`__init__.py` defines small typed `Protocol` interfaces for sources, artifacts, parsing, inference,
repositories and explicit units of work.
`UnitOfWorkFactory.scan_guard` scopes renewable scan ownership and fences writes through child tasks.
`SourceMissingError` distinguishes absent sources from temporary retrieval/access failures.
`IngestionSkills` separates ontology/scope labeling from scoped extraction with independent replay IDs.
Optional `RequestControlledSkills` scopes a `ModelRequestLimiter` around evaluation requests;
providers reserve each actual request, including retries, by deployment and reset on cancellation.
Knowledge search supports exact-source/level filters and exclusive UUID cursors in stable order.
`AssertionWriter` rephrases a version against its exact evidence; original candidates stay immutable.
Knowledge counts provide typed workspace totals; `PipelineProgress` observes scan/stage results.
Processing task inspection is bounded and uses exclusive UUID cursors in stable order.
`InspectionRepository` provides bounded source, graph, backlog and allowlisted table queries.
`ArtifactStore` preserves immutable content-addressed originals.
`SemanticVerifier` and `EmbeddingPort` are opt-in boundaries for generic, evidence-bound comparisons.
Verifier `method` identifiers are adapter-defined; `Verification.status` distinguishes completion,
failure and context limits. Adapters must mark incomplete results; legacy `model_failed` and
`context_limit` methods are recognized as incomplete. Successful `unknown` is not a failure.
Embedding `usage_complete` is `True` only when all calls are metered (including no calls), `False`
when incomplete, and `None` when unknown; it is independent of whether prices are available.
`workflow.py` defines stage/check contracts and execution, resolution and issue repositories.
Its workflow repository stores typed embedding and judgment replay entries, keyed by provider and
exact ordered input. Implementations must reject invalid entries and permit their replacement.
Failed cache lookups abort rather than silently making provider calls; hits do not repeat usage.
Workflow lease acquisition and strict existing-owner renewal are separate operations.
Future stores must preserve these semantics through `tests\contract\test_resolution.py`.

These contracts use domain values; do not import concrete adapters or provider/transport SDKs.
Add or extend a port only for a real interchangeable boundary, not every helper.
Wire implementations in `src\ekb\runtime.py` and preserve behavior with adapter/repository tests.
See `docs\plugins.md`; strict typing applies to this package.
