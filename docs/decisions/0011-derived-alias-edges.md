# 0011: Derive alias edges from reviewed findings

Status: accepted.

Accepted alias decisions already identify immutable findings with ordered concept IDs. Resolve alias
edges from the applicable decision heads and those findings instead of storing a duplicate
`concept_aliases` table. Keep the global transaction lock, bounded decision/finding queries,
identity/type validation, cycle detection, historical effective dates and explicit reversal.
Guarded pre-v1 recreation removes the obsolete EKB-owned table; normal startup does not.

On a disposable PostgreSQL fixture with 10,000 findings/decisions and 50 aliases, the old lookup
sorted 10,000 decision rows and read the alias projection separately. The replacement used a
partial alias index and indexed decision-head lookups; one local run took 3.9 ms versus 26.3 ms
for the old queries combined. These synthetic timings are not a production capacity estimate.

Keep `processing_runs` distinct from `pipeline_executions`: standalone scan summaries and
pipeline/stage/check executions have different payloads, statuses and selection/lease semantics.
Keep parse completion and independently fingerprinted skill checkpoints separate, including empty
parses. These distinctions avoid a tagged catch-all or ambiguous current-output selection.
