# 0015: Reviewed offline demo snapshot

## Context

A new checkout should expose populated knowledge without Foundry, parsing, or paid inference.
A raw development backup can contain local-only publications, credentials, operational details
and human governance state; the repository normally forbids runtime data.

## Decision

Allow only a reviewed demo bundle with explicit attribution, a fixed redistributable source
allowlist, archive/original hashes and a schema fingerprint. Export from a disposable copy;
retain exact evidence and stable IDs, remove excluded sources and cross-source configuration,
and omit governance, solution designs, processing state and model replay.

Restore into a separate Compose project and artifact directory, transactionally and only into
an empty database. Reuse an existing matching demo without resetting it. The launcher invokes
the existing web CLI with inference and cloud telemetry disabled, overriding local configuration.
Runtime remains the sole composition root; no new application port or provider is needed.

## Consequences

The demo is immediately inspectable but deliberately incomplete, with no approved policy or
corporate annual reports. Schema changes require a refreshed snapshot or a matching checkout.
This exception does not authorize committing other runtime data or arbitrary corpus downloads.
