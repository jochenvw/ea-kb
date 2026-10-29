# Agent coding standards

These instructions apply throughout this repository. Preserve a small, executable Enterprise
Knowledge Base (EKB), with exact provenance and replaceable infrastructure.

Keep product descriptions, domain logic and synthetic examples enterprise- and industry-neutral.
Publisher-specific names belong in actual source attribution and corpus metadata, not product branding.

## Work minimally

- Read the nearest folder README, affected implementation and tests before editing. Follow existing
  patterns; implement the current request, not adjacent backlog or speculative future capabilities.
- Choose the smallest complete solution. Prefer the standard library and installed dependencies.
  Add a dependency, abstraction or configuration option only for a concrete requirement.
- Keep functions and modules cohesive. Prefer composition and explicit calls over inheritance
  hierarchies, generic base classes, decorators that hide control flow, and framework machinery.
- Share genuinely repeated behavior; do not generalize a single use case into a platform.
- Keep changes surgical. Include necessary dependent call sites, tests and documentation; avoid
  unrelated refactoring. Comment reasons and non-obvious invariants, not obvious operations.

## Keep folder guides current

- Every meaningful version-controlled folder needs a short `README.md`, especially packages,
  adapters, plugins, extensions, interfaces, scripts, configuration and deployment folders.
  Add the guide when creating a folder; update it in the same change when responsibilities,
  entry points, extension contracts or verification commands change.
- Aim for 5-15 lines: purpose, key files/entry points, inputs/outputs or callers, important
  boundaries, and the most useful test or parent-document reference. Give an agent a starting
  point, not a second architecture manual or a generated file listing.
- Keep detailed design in `docs\architecture.md`, plugin contracts in `docs\plugins.md`, and coding
  rules here. Link to those sources instead of duplicating them in every guide.
- Do not add guides to ignored runtime/generated output. Raw fixture-data subfolders are exempt:
  document them in `tests\fixtures\README.md` so navigation text does not become ingestion input.
- `tests\unit\test_folder_readmes.py` checks tracked and new non-ignored files in a Git checkout,
  including future plugin/extension folders, and fails for missing or empty guides. Run it before
  committing structural changes; the normal pytest suite also includes it.

## Write idiomatic, typed Python

- Target Python 3.13 and use `uv` with the committed lockfile. Verify installed library APIs rather
  than guessing signatures or introducing compatibility shims without a supported use case.
- Use clear names, ordinary control flow, built-in generic types, `X | None`, `pathlib.Path`,
  context managers and small functions. Prefer readability over compressed or clever expressions.
- Type public functions and adapter contracts. Use Pydantic v2 for validated domain and boundary
  schemas; keep known structures typed rather than spreading unstructured dictionaries or `Any`.
- Keep deterministic domain transformations synchronous and free of side effects. Do not mark
  functions `async` when they have nothing to await.
- Follow `pyproject.toml`: Ruff formatting, 100-character lines, and basedpyright, with strict
  checking for `domain` and `ports`. Fix causes instead of weakening checks or adding broad ignores.

## Use async for I/O, with bounded work

- Use native asynchronous HTTP, database and Azure clients. Reuse clients and connection pools;
  manage lifetimes explicitly with context managers and close resources on failure and shutdown.
- Keep blocking filesystem operations, document parsers, optical character recognition (OCR), and
  synchronous SDK calls off the event loop, using `asyncio.to_thread` where appropriate. Async
  syntax does not make CPU-bound work parallel.
- Prefer `asyncio.TaskGroup` for related tasks. Bound queued work and active workers with queues
  and stage-specific limits. A semaphore alone does not bound an unbounded list of created tasks.
- Stream discovery and large responses where practical. Bound response sizes, query results,
  traversal depth, retries and timeouts. Avoid loading entire inventories just to schedule work.
- Let cancellation propagate; release resources in `finally` or context managers. Do not leave
  detached work running or assume cancellation stops an already-running parser thread.
- Retry transient failures only, with bounded exponential backoff and jitter. Do not retry invalid
  configuration or bad data indefinitely, or silently turn failures into successful empty results.
- Keep database transactions short. Never share an asynchronous database session across tasks or
  hold a transaction open while waiting for downloads, parsing or model inference.

## Preserve boundaries and extension contracts

- Keep the modular monolith: `domain` owns schemas and rules; `ports` owns small `Protocol`
  interfaces; `pipeline` and `services` coordinate use cases; `adapters` own infrastructure.
- Domain and port modules must not import concrete adapters, ORM, web, UI, cloud or model SDKs.
  Keep ORM rows inside the persistence adapter; return domain models through repository ports.
- Wire concrete implementations and resource ownership in `runtime.py`, the composition root.
  Inject dependencies explicitly; do not add global business state, service locators or a
  dependency-injection framework.
- Extend an existing port when it fits. Add a new port only for a real boundary requiring an
  interchangeable implementation, not for every helper. Do not build unused future adapters.
- Use the existing `ekb.connectors` package entry-point group for connector plugins. Keep discovery,
  configuration and activation separate: registration alone must not enable network access.
- Verify plugin loading and adapter behavior with contract tests. Replacement implementations
  must preserve identity, evidence, checkpoint and transaction semantics, not just method names.
- Keep provider-specific capability checks, credentials, retry details and optional SDK imports
  in adapters. Do not assume structured output, vision or embeddings are supported.
- Keep CLI, TUI, web and MCP interfaces thin and backed by shared application services.

## Protect efficiency, provenance and durable state

- Prefer deterministic parsing and rules to model calls when sufficient. Keep inference behind its
  port; do not add autonomous agents, extra enrichment passes or exhaustive graph extraction
  without an evaluated requirement.
- Reuse revision checks, conditional requests, fingerprints and durable checkpoints. Unchanged
  inputs must not trigger unnecessary parsing, OCR or inference. Update fingerprints when parser,
  prompt, schema or relevant processing configuration changes.
- Preserve originals, exact evidence, stable identifiers and historical occurrences. Never invent
  missing text, scope, authority or successful retrieval. A candidate is not approved policy.
- Keep publisher labels, publication maturity, semantic versions, governance and scoped canonical
  adoption independent. Retain unknown versions; do not turn edition labels into semantic versions
  or automatically promote a draft, preview, published document or newest revision.
- Preserve explicit unit-of-work commits, rollback on failure, atomic segment persistence and
  ownership fencing. Make retries idempotent; do not claim exactly-once external model billing.
- Use Alembic for schema changes. Keep database and artifact-store changes consistent; switching
  storage settings does not migrate historical originals.
- Never commit credentials, downloaded corpus files or runtime data. Keep telemetry free of source
  text, authorization headers and sensitive exception details; payload capture remains opt-in.

## Verify and commit coherent changes

- Add focused tests for new behavior and fixed failures, including relevant edge cases. Reuse
  existing fixtures and contract tests; avoid tests that merely repeat implementation details.
- Start with affected tests using `uv run pytest -q` and appropriate path selectors. Broaden for
  cross-cutting changes. PostgreSQL tests require `EKB_TEST_DATABASE_URL` and a disposable-schema
  setup described in `docs\usage.md`; skipped database tests are not evidence of database correctness.
- Keep unit tests offline and deterministic. Do not introduce paid inference or live cloud calls
  into default verification. Report offline contracts separately from live integration results.
- Every changed capability needs matching tests/evals. Use `uv run ekb test` for combined verification;
  include `--ui` for browser changes (frontend formatting/types/build and Playwright). Rebuild and
  commit generated assets with their source; source fingerprints and licenses must stay current.
  Model/prompt/retrieval changes also need source-grounded, split-isolated qualification cases.
  Keep strong-teacher labels provisional, export only training data, and require measured live
  quality plus priced cost/latency gates before calling a cheap model qualified. See `docs\evaluation.md`.
- Run the existing quality gates for code changes:

  ```powershell
  uv run ruff check src tests migrations scripts
  uv run ruff format --check src tests migrations scripts
  uv run basedpyright
  ```

- Run `uv run alembic check` for persistence changes and `uv lock --check` for packaging changes.
  Documentation-only edits need no application test run unless they change executable examples.
- If an npm-based tool cannot access its upstream, use its documented registry configuration with
  `https://packagefeedproxy.microsoft.io/npm/`. Keep package safety restrictions enabled.
- Update usage and contract documentation when behavior changes. Record significant architecture
  decisions briefly under `docs\decisions`; do not create planning documents as a substitute for code.
- Commit completed, coherent changes with descriptive messages and related tests/docs. Preserve
  unrelated user edits; do not rewrite history or push without instruction. State blockers and
  unverified behavior plainly rather than claiming completion.

See `docs\usage.md`, `docs\architecture.md` and `docs\plugins.md` for current workflows and contracts.
