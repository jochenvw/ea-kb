# EKB Phase 2 — Microsoft Security Benchmark Ingestion, Lineage and Semantic Reconciliation

## Context

Continue developing the existing Enterprise Knowledge Base (EKB) Python application. Do not create a new project.

The application already has or is implementing:

- A modular, async-first Python architecture.
- PostgreSQL persistence with interchangeable repository adapters.
- Source connectors, artifact storage and incremental indexing.
- Objective and Requirement semantic primitives.
- Evidence-backed relationships and canonical version history.
- OpenTelemetry observability.
- Local TUI and containerized web interfaces.
- Microsoft Agent Framework behind an inference abstraction.

**Your task is to extend the existing application with real Microsoft security benchmark content, automated downloading, control extraction, historical version comparison, explicit framework mappings and semantic reconciliation.**

Implement a working, tested vertical slice. Do not stop at scaffolding.

---

## 1. Sources to retrieve

Download and ingest the following actual Microsoft benchmarks.

### A. Azure Security Benchmark v2 — historical

Official source:

https://learn.microsoft.com/en-us/security/benchmark/azure/overview-v2

Use this as historical context for the earlier Azure-specific benchmark.

### B. Microsoft Cloud Security Benchmark v1

Official source:

https://learn.microsoft.com/en-us/security/benchmark/azure/overview-mcsb-v1

Network Security controls:

https://learn.microsoft.com/en-us/security/benchmark/azure/mcsb-network-security

This is the primary earlier edition to compare against MCSB v2.

### C. Microsoft Cloud Security Benchmark v2 (preview)

Official source:

https://learn.microsoft.com/en-us/security/benchmark/azure/overview

Network Security controls:

https://learn.microsoft.com/en-us/security/benchmark/azure/mcsb-v2-network-security

This edition explicitly supersedes MCSB v1.

### D. Published cross-framework mappings

MCSB v2 to CIS Controls:

https://learn.microsoft.com/en-us/security/benchmark/azure/mcsb-v2-cis-controls-mapping

Also retrieve the NIST, ISO and Azure Policy mappings embedded in individual control pages.

Include, where published:

- NIST SP 800-53 Rev. 4
- NIST SP 800-53 Rev. 5
- NIST CSF 2.0
- ISO/IEC 27001:2022
- CIS Controls
- Azure Policy definitions

Do not incorrectly treat a mapping to an older NIST revision as equivalent to a newer revision.

### E. Azure Policy references

Retrieve linked Azure Policy definitions and metadata for at least the selected Network Security controls, particularly NS-6.

Preserve policy definition IDs and version identifiers where available.

Do not confuse Azure Policy definition versions with benchmark or concept versions.

---

## 2. Actual downloading is mandatory

Extend the existing source manifest and downloader.

**Do not merely save URLs or overview-page summaries. The code must fetch the actual benchmark content.**

Implement a `MicrosoftLearnBenchmarkConnector` using existing source connector interfaces.

The connector must:

1. Fetch the benchmark overview.
2. Discover the linked control-domain pages.
3. Download individual control-domain articles.
4. Discover official offline spreadsheet downloads where available.
5. Download and parse those spreadsheets when accessible.
6. Preserve source URLs, HTTP metadata, timestamps, content hashes and original content.
7. Extract controls, subcontrols and cross-framework mappings.
8. Avoid unrelated navigation pages and unrestricted web crawling.
9. Support bounded asynchronous downloads.
10. Resume interrupted downloads.
11. Skip unchanged content on subsequent scans.

Prefer Microsoft's official structured source files when available.

Investigate Microsoft Learn's source Markdown or GitHub repositories as possible structured ingestion sources.

Use published official content rather than a search-result snippet.

### Download validation

Verify that the downloaded content is real and usable:

- HTML includes the expected benchmark control content.
- Spreadsheets have valid workbook structure.
- Control sections contain recognizable control IDs.
- Extracted mappings reference identifiable framework controls.
- Download failures are reported, not silently replaced with synthetic content.

Some benchmarks may provide spreadsheets while others provide HTML or Markdown only. Support those formats without requiring them all.

Do not invent nonexistent v2 spreadsheets.

---

## 3. Repository destinations

Store downloaded assets under:

```text
data/test-corpus/public/
└── microsoft_security_benchmark/
    ├── azure_security_benchmark_v2/
    │   ├── raw/
    │   └── normalized/
    ├── mcsb_v1/
    │   ├── raw/
    │   └── normalized/
    ├── mcsb_v2/
    │   ├── raw/
    │   └── normalized/
    ├── mappings/
    │   ├── nist/
    │   ├── iso/
    │   ├── cis/
    │   └── azure_policy/
    └── manifest.json
```

Use the existing artifact-store interface rather than hardcoding filesystem access in the connector.

The local filesystem is the default destination. The same content should be storable through the Azure Blob adapter.

Add a CLI command:

```bash
uv run ekb sources sync microsoft-security-benchmark
```

And a focused option:

```bash
uv run ekb sources sync microsoft-security-benchmark \
  --versions mcsb-v1,mcsb-v2 \
  --domains NS
```

Integrate the connector into the normal scan workflow.

---

## 4. Extract structured controls before using AI

Microsoft publishes semistructured security control definitions. Use that structure.

For each control extract:

- Benchmark family.
- Published benchmark edition.
- Control domain.
- Control ID.
- Control name.
- Security principle.
- Risk to mitigate, if present.
- Azure-specific implementation guidance.
- Subcontrols and their identifiers.
- Criticality, if present.
- External control mappings.
- Azure Policy references.
- Exact source URL and section.
- Retrieval revision and timestamp.

For example:

```json
{
  "benchmark": "MCSB",
  "edition": "v2-preview",
  "control_id": "NS-6",
  "title": "Deploy web application firewall",
  "semantic_type": "requirement",
  "scope": {
    "platform": "Azure",
    "domain": "Network Security"
  },
  "source": {
    "url": "https://learn.microsoft.com/en-us/security/benchmark/azure/mcsb-v2-network-security",
    "section": "NS-6"
  }
}
```

This is an illustrative partial schema, not the complete control or its mappings.

Do not classify every benchmark recommendation as a mandatory enterprise policy.

Microsoft's recommendation and criticality levels are source metadata. Enterprise authority is separate.

Use deterministic HTML, Markdown and spreadsheet parsing first.

Use LLM extraction only for genuinely unstructured portions.

---

## 5. Preserve the minimal ontology

Continue using only two semantic concept primitives:

- Objective
- Requirement

Do not introduce separate semantic node types for every security framework.

Represent framework membership, benchmark editions, source identities, mappings, scope and authority through attributes, records and relationships.

Permitted relationships include:

- `DERIVES_FROM`
- `SUPPORTS`
- `DEPENDS_ON`
- `OVERLAPS_WITH`
- `TENSION_WITH`
- `CONFLICTS_WITH`
- `MAPS_TO`
- `SUPERSEDES`

Distinguish published lineage from model-inferred semantic similarity.

For example, a Microsoft-to-NIST mapping is not necessarily a derivation relationship.

Store `MAPS_TO` with optional coverage (`full`, `partial`, `unknown`) and supporting evidence.

Never infer full compliance from the existence of a mapping.

---

## 6. Version and successor detection

This is a primary objective of the implementation.

### Benchmark edition lineage

Recognize published relationships such as:

Azure Security Benchmark → Microsoft Cloud Security Benchmark v1 → Microsoft Cloud Security Benchmark v2.

Where Microsoft explicitly states that one edition supersedes another, preserve that statement with its source evidence.

### Control-level lineage

Do not assume every control with an identical ID is unchanged or automatically the same semantic concept.

Compare:

- Control identifiers.
- Names.
- Security principles.
- Applicable scopes.
- Implementation guidance.
- Subcontrols.
- External mappings.
- Azure Policy references.

Generate candidate relationships:

- Same semantic concept.
- Updated version.
- Superseded requirement.
- Similar but distinct requirement.
- Newly introduced requirement.
- Removed requirement.
- Split or merged requirement.

Store confidence, evidence and review status.

### Semantic version proposal

Use MAJOR.MINOR.PATCH semantics:

- PATCH: Nonsemantic/editorial change.
- MINOR: Additive, backward-compatible semantic change.
- MAJOR: Breaking change affecting existing requirements or implementation expectations.

Do not derive semantic version numbers mechanically from Microsoft benchmark edition labels.

Do not consider a changed document hash sufficient evidence for a semantic change.

A new benchmark edition may add guidance to an existing concept without introducing a breaking change.

Generate proposed semantic changes; do not automatically approve canonical transitions.

---

## 7. Duplicate detection

Implement a layered strategy.

**Level 1: Exact identity**

Match official framework identifiers within their proper namespace and edition.

**Level 2: Structural similarity**

Compare normalized names, control statements and section structure.

**Level 3: Embedding similarity**

Retrieve candidates using embeddings within a suitable domain and scope.

**Level 4: Semantic verification**

Use a configurable small model to determine whether two control occurrences represent the same underlying requirement.

Possible outcomes:

- Exact duplicate occurrence.
- Equivalent semantic concept.
- Compatible extension.
- More specific implementation guidance.
- Different requirements sharing vocabulary.
- Potential conflict.

Never automatically merge simply because two texts have high embedding similarity.

Preserve every original occurrence, even when multiple sources resolve to one semantic concept.

---

## 8. Cross-framework provenance

One of the key tests is that Microsoft already publishes mappings to other control frameworks.

Do not waste model inference rediscovering these relationships.

Use deterministic mapping extraction.

For example:

```text
NIST SP 800-53 SC-7
        |
        | Published mapping
        v
MCSB v1 / NS-6
        |
        | Semantic lineage candidate
        v
MCSB v2 / NS-6
        |
        | Azure implementation guidance
        v
Azure WAF capabilities
```

The arrows above describe different relationship semantics. Do not collapse them all into `DERIVES_FROM`.

Preserve exact source references for every relationship.

Build a query to retrieve the complete evidence chain from an MCSB control to its mapped NIST or ISO control.

Where upstream NIST OSCAL controls are already ingested, link to those existing identities using their exact framework identifiers.

Do not create duplicates merely because NIST is referenced by both the native OSCAL catalog and Microsoft documentation.

For links that cannot be confidently resolved, preserve the published reference as an unresolved external identifier.

---

## 9. Specific NS-6 test scenarios

Use NS-6 (Deploy web application firewall) as the first acceptance case.

### Test A — Historical presence

Verify NS-6 can be extracted from MCSB v1 and v2.

Preserve the distinct source occurrences.

### Test B — Semantic equivalence

Determine whether both editions express the same core security principle.

Avoid creating two unrelated canonical concepts merely because they came from different benchmark editions.

### Test C — Guidance evolution

Compare implementation guidance between v1 and v2.

Identify additions, removals and changed recommendations.

Preserve any v2 subcontrol structure.

### Test D — NIST mapping

Extract Microsoft's published mappings to NIST controls.

Where the upstream NIST OSCAL catalog is available, resolve the control references to their corresponding identities and correct revisions.

Never invent a NIST requirement that is not present in the source.

### Test E — Azure alternatives

Capture published guidance mentioning Azure Application Gateway, Azure Front Door and other supported WAF deployment options.

Do not interpret alternatives as simultaneous mandatory requirements.

### Test F — Azure Policy

Retrieve at least one actual policy reference associated with WAF.

Preserve its official identifier, resource scope and relevant metadata.

### Test G — Canonical source

Preserve the fact that Microsoft publishes the benchmark, but do not automatically designate either edition as the organization's canonical architecture standard.

Maintain the distinction between publisher authority and enterprise adoption.

---

## 10. Historical and current queries

Implement query operations that answer:

1. Which benchmark edition supersedes MCSB v1?
2. What changed for NS-6 between v1 and v2?
3. Which controls exist in v2 but not v1?
4. Which NIST controls are mapped to NS-6?
5. Which Azure implementation guidance is associated with NS-6?
6. Which requirements are semantically equivalent across the editions?
7. Which controls have changed external mappings?
8. What source passage establishes the benchmark successor relationship?

Use PostgreSQL for persistence and graph traversal through the existing repository ports.

Do not introduce Neo4j.

Expose these queries through the CLI and existing inspection interfaces.

Add a simple relationship visualization for NS-6 in the web UI where the graph explorer already exists.

---

## 11. Observability

Use the existing OpenTelemetry setup.

Create spans for:

- `benchmark.discover`
- `benchmark.download`
- `benchmark.parse`
- `benchmark.extract_controls`
- `benchmark.extract_mappings`
- `benchmark.compare_editions`
- `benchmark.match_concepts`
- `benchmark.propose_lineage`
- `benchmark.persist`

Track:

- Documents discovered/downloaded/skipped.
- Controls extracted per edition.
- Mappings extracted.
- Exact matches.
- Semantic matches.
- New and removed controls.
- Proposed version changes.
- LLM calls and token usage.
- Download and parsing failures.

Retain local disk traces and logs, and preserve Application Insights compatibility.

---

## 12. Evaluation and tests

Create a small golden evaluation dataset based on the actual downloaded benchmarks.

Include tests for:

- Stable control identity extraction.
- Mapping identifier accuracy.
- NS-6 extraction across editions.
- Explicit benchmark supersession.
- Equivalent concepts across sources.
- Changes in implementation guidance.
- Missing or changed mappings.
- Unknown rather than fabricated relationships.
- Idempotent repeated ingestion.
- Selective reprocessing after parser changes.

Test three separate capabilities:

**Extraction accuracy:** Did we correctly retrieve Microsoft's source data?

**Reconciliation accuracy:** Did we identify concept equivalence and version differences correctly?

**Lineage accuracy:** Can every relationship be traced back to published evidence?

Report precision and recall where a labeled dataset permits measurement.

Do not invent evaluation results.

---

## 13. Deliverables

Produce working code, migrations, tests and documentation.

Include:

1. Updated source manifest.
2. Working Microsoft benchmark downloader.
3. Local artifact files from the actual download run.
4. Structured control and mapping parser.
5. Historical comparison and semantic reconciliation.
6. Persisted provenance and successor relationships.
7. CLI commands for synchronization and comparison.
8. Basic visualization/query support.
9. Golden tests and evaluation report.
10. OpenTelemetry traces demonstrating the execution.

Document source retrieval failures, missing published files, and unresolved external mappings explicitly.

## Definition of done

The implementation is complete when I can execute:

```bash
uv run ekb sources sync microsoft-security-benchmark
uv run ekb scan
uv run ekb concepts search "web application firewall"
uv run ekb compare mcsb-v1 mcsb-v2 --control NS-6
uv run ekb lineage "mcsb-v2:NS-6"
```

And inspect:

- The actual downloaded benchmark content.
- Its extracted NS-6 controls.
- The relationship between old and new editions.
- Proposed semantic changes.
- Explicitly published NIST mappings.
- Azure implementation recommendations.
- Original evidence for each relationship.

**Final instruction:** Do not substitute synthetic examples for real Microsoft sources. Actual download, extraction, persistence and verification are mandatory. If a source is inaccessible or no longer published, report that limitation accurately and continue with verified available sources.


### Additional requirement — Generalized draft and pre-release versioning

Implement **SemVer 2.0 pre-release support for all knowledge sources and concepts**, regardless of publisher, source type, or format.

This applies to external standards (NIST, ISO, Microsoft), regulations, internal enterprise documents (e.g., architecture guidelines), objectives, requirements, and architecture decisions.

**1. Detect publication maturity**

During ingestion, identify explicit maturity indicators such as:

- Draft, working draft, proposed, consultation
- Preview, alpha, beta, release candidate
- Final, published, approved
- Deprecated, withdrawn, superseded

Inspect document metadata, titles, version labels, approval records, and relevant document content.

Do not infer draft status merely because a document lacks an approval statement. Use `unknown` when evidence is insufficient.

**2. Normalize versions**

Support SemVer pre-release identifiers:

- `1.0.0-draft.1`
- `1.0.0-preview.2`
- `2.0.0-rc.1`
- `2.0.0`

Preserve the publisher's original version and status alongside the normalized version.

Do not invent a meaningful major/minor/patch classification without sufficient evidence. If the source has no version, retain an unknown source version and use content revisions until a semantic version can be established.

**3. Separate version, maturity, and authority**

Maintain independent properties:

- `semantic_version` — Normalized SemVer, if known or established.
- `publisher_version` — Original source version label.
- `publication_status` — Draft, preview, final, withdrawn, etc.
- `governance_status` — Proposed, under review, approved, rejected, etc.
- `canonical_status` — Whether this version is authoritative within a particular scope.
- `effective_from` / `effective_until` — Applicability period.

A draft can be internally approved for a limited scope without becoming the enterprise-wide canonical version.

**4. Preserve historical transitions**

Track draft revisions, publication events, approvals, and predecessor/successor relationships.

Examples:

`1.0.0 → 2.0.0-draft.1 → 2.0.0-draft.2 → 2.0.0`

Never automatically promote a draft or preview to canonical status.

**5. Evidence and testing**

Every detected maturity status must reference its source evidence.

Include tests for:

- Microsoft benchmark preview releases.
- Internal enterprise draft architecture guidelines.
- External consultation documents.
- Approved internal standards.
- Documents without explicit version or publication status.
- Drafts that are withdrawn without becoming final.
- Multiple concurrent draft branches.

Ensure the TUI, web UI, API, and MCP expose publication status and distinguish draft knowledge from authoritative requirements.

**Core rule:** Publication maturity, semantic version, and canonical authority are separate dimensions. Never assume that the newest or most mature document is automatically authoritative.