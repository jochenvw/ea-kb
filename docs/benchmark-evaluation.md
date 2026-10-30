# Benchmark evaluation

The captured dataset `tests\golden\benchmarks.json` contains five **actual** downloaded source
excerpts: ASB v2 NS-6, MCSB v1/v2 NS-6, the v2 publication/succession notice and an actual built-in
Application Gateway WAF policy. It includes official URLs, licensing/attribution, full-original
SHA-256 digests and distinct excerpt digests. Source excerpts were checked against retained originals.
Expected identifiers and reconciliation labels were reviewed separately from parser predictions.

```powershell
uv run ekb eval benchmarks
```

The offline run on 2026-10-29 produced the following measured results:

| Capability | Result |
|---|---|
| Control identifier extraction | 3 TP, 0 FP, 0 FN; precision/recall 1.0 |
| Framework mapping extraction | 26 TP, 0 FP, 0 FN; precision/recall 1.0 |
| Principles, subcontrols, alternatives, maturity and policy metadata | Accuracy 1.0 on applicable captured cases |
| Reconciliation outcome / proposed change / mapping changes | Accuracy 1.0 on three reviewed pairs |
| Published edition succession | 1 TP, 0 FP, 0 FN; precision/recall 1.0 |
| Exact passage grounding | 1.0 |
| Paid model calls / tokens | 0 / 0 |

The negative reconciliation pair deliberately reuses `NS-6`: ASB v2 recommends simplifying network
rules, while MCSB v1 recommends a WAF. Matching identifiers do not establish equivalent meaning.
The WAF pair receives a **proposed** MINOR guidance classification; numeric SemVer remains unknown
and backward compatibility requires review. This does not approve an enterprise transition.

Full predictions and current metrics persist under `data\benchmark-evaluation.json`. Offline runs
verify excerpt integrity, not a fresh download of every original. Integration tests separately
exercise PostgreSQL native-label resolution, unchanged ingestion, parser replay, provider caching
and read-only interfaces. The actual corpus additionally resolved all five Rev.5 WAF mappings and
nine published policy references.

These are small, source-specific regression checks, **not** whole-benchmark, embedding, compliance
or paid-model quality claims. Live optional model/provider quality and Azure deployment remain
unverified. `--configured` explicitly opts into the configured small model and potential charges.

## Actual-corpus acceptance

All five phase-two acceptance commands completed successfully against the retained corpus. The repeat sync
skipped 221 unchanged assets; ingestion skipped 222 cached records, with no failures or model calls.
ASB v2 / MCSB v1 / MCSB v2 contain 70 / 86 / 81 controls respectively.

NS-6 inspection verified five native Rev.5 NIST bindings, nine actual policy definitions and 23
exact passage ranges against line-ending-normalized originals. The proposed MINOR remains
review-only with unknown numeric SemVer; the control source is unknown maturity while its edition
is preview. No authority events were created. Unavailable or invalid external targets stay unresolved.

Local command outputs, verification counts and representative successful spans for all nine required
benchmark stages are retained under `data\benchmark-acceptance`. These ignored runtime artifacts
complement the committed golden dataset; they are not synthetic fixtures or a fresh extraction claim.
