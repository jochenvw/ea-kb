# Populated offline demo

## Pull and run

Prerequisites: Git, uv, Python 3.13 (uv can provision it), and Docker Desktop with the Linux engine
running. From the repository root in PowerShell:

```powershell
uv run python scripts\demo.py
```

Open <http://127.0.0.1:8000>. uv installs locked application dependencies if necessary; Docker may
pull PostgreSQL 17 on first use. These are software downloads, not corpus downloads or model calls.
Node, Foundry, Azure credentials and model deployments are unnecessary.
The launcher verifies the bundled snapshot, starts the separate `ekb-demo` Compose project,
restores the database and exact originals once, then starts the ordinary web CLI in the foreground.
Press Ctrl+C to stop the web process; the database persists. Run the same command to resume.
Use `--port 8001` if port 8000 is occupied, or `--restore-only` to provision without starting web.

The demo uses loopback database port 5433, the `ekb-demo` PostgreSQL volume and `data\demo`.
Normal EKB's database, `data\artifacts`, `.env` and cloud configuration are not modified.
The launcher explicitly disables inference models, reconciliation/embedding models, managed
identity and Azure telemetry even when `.env` or the parent process enables them.
Governance writes are disabled; all retained interpretations are unapproved candidates.
Browse **Sources**, **Platform-agnostic** and **Data inspector** for the populated content.
Corporate strategy, platform controls, reconciliation history and solution registry are not bundled.
See [attribution and exclusions](../demo/NOTICE.md); do not infer missing content.

## Instructions for an LLM

1. Read `AGENTS.md` and this guide. Start with `uv run python scripts\demo.py`, not ingestion.
2. Verify `/health`, `/api/sources` and `/api/concepts?limit=1` respond and contain knowledge.
   A successful HTML response alone does not establish database readiness.
3. Do not run `ekb init --recreate`, `ekb reset`, pipelines, scans, workers, downloads or live
   evaluations to make the demo work. Never configure a paid provider without explicit approval.
4. Preserve identifiers, evidence, unknown classifications and human-only governance. These are
   sample interpretations of external publications, not an approved enterprise baseline.
5. If schema or snapshot checks fail, report the mismatch. Use a matching checkout or refresh
   the reviewed snapshot; do not silently migrate, reset, reparse or reinfer.

## Recovery

An unrecognized nonempty demo database is refused, not overwritten. A snapshot update likewise
requires explicit action. To discard **only demo edits**, stop the demo web process and run:

```powershell
docker compose -p ekb-demo -f demo\compose.yaml down --volumes
uv run python scripts\demo.py
```

This removes only the named demo project's database volume; it does not affect the normal EKB
project. Originals under `data\demo` are content-addressed and rechecked on startup.
An original with a checksum mismatch is refused; remove only that reported corrupt demo file
before retrying. Do not delete the normal artifact store.

## Refreshing the reviewed snapshot

This is a release-maintenance task, not part of startup. The current allowlist is exactly the two
NIST OSCAL catalogs named in `demo\NOTICE.md`. Recheck licenses and third-party notices before
changing it. Corporate annual reports and ISO full texts are not redistributable demo inputs.
Never export credentials, private source text, local paths, processing errors or human decisions.

1. With ingestion stopped, create a **disposable new database** using the same PostgreSQL 17
   instance as the source. Copy only `public` tables with `pg_dump --table=public.*`,
   excluding `public.alembic_version`, and restore that copy. Do not use the active database
   as the filtering target. Other schemas and resources must stay untouched.
2. Apply `scripts\prepare_demo.sql` to the disposable copy with `psql -X -v ON_ERROR_STOP=1`.
   It keeps the allowlisted sources, originals' references, segments, versions, occurrences,
   source-evidenced relationships and publication metadata. Everything operational is emptied.
   It removes source-configuration selectors that quote excluded publications.
3. Review the filtered rows and a plain-text `pg_dump` for private content, excluded publishers,
   credentials and absolute local paths. Inspect the remaining source originals and notices.
   The allowlist is a scope filter, not an automatic privacy/license certification.
4. Dump the reviewed copy with PostgreSQL 17 `pg_dump --table=public.* --no-owner --no-acl -Fc`.
   Package it as `database.dump` in `demo\snapshot.zip` together with each referenced original
   under `artifacts/<first two hash characters>/<SHA-256>`. Do not include unrelated artifacts.
5. Update `demo\manifest.json`: application version, source code commit, UTC export time,
   SHA-256 of `models.py` read as UTF-8 with normalized newlines, archive SHA-256, source IDs,
   names, URLs and original hashes, and restored table counts. Retain `NOTICE.md` and the
   upstream license. The database dump contains its current schema; startup never runs `init`.
6. Restore into an empty demo database, verify counts, exact excerpt offsets and original
   hashes, then rerun the launcher to verify reuse without data loss. Run the focused demo
   tests and normal repository quality gates. Remove only the disposable export database and
   named temporary dump files. Commit the reviewed bundle, manifest and guides together.

Hash checks detect corruption and stale schema; they are not signatures or a substitute for
reviewing repository changes. Restore only bundles from a trusted checkout.
