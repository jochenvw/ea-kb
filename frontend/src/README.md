# Workspace presentation

`app.tsx` leads with shared language and a source-to-scoped-standard consolidation journey.
Workspace, Consolidate, Knowledge, Reference and Operations support architect tasks and inspection.
Ingestion execution and monitoring are CLI-only; no browser scan control or dashboard is provided.
`sources.tsx` inspects individual documents and their source-bound versions.
`network.tsx` navigates evidence/relationship graphs; source evidence remains document-specific.
`review.tsx` separates the active backlog from the read-only ledger in `reference.tsx`.
`reference.tsx` also provides bounded, linked table previews and safe operational log tails.
`ontology.tsx` reads canonical definitions/IRIs from `/api/ontology`; badges and inline definitions reuse it.
`ontology-diagram.tsx` lazily renders Mermaid from that catalog; OWL classes are grouped by the
three knowledge layers and only declared domains/ranges get arrows.
`main.tsx` mounts the app; `styles.css` defines responsive layout.
`knowledge.tsx` separates company intent, platform-agnostic architecture and platform-specific
controls while keeping evidence first; scoped authority and history have separate detail tabs.
Strategic goals show their enterprise, business-unit or supporting-target level in lists and details.
Evidence, authority and proposal defaults follow the selected version, not an unrelated latest version.
Company, platform-agnostic and platform-specific concepts have separate routes and graph lanes.
Use real API records, abort stale reads, disclose query limits and preserve unknown values.
Publisher maturity, semantic versioning and scoped enterprise authority must stay distinct.
Native review forms retain server-side Basic authentication, origin checks and immutable auditing.
Use `components\ui` and the Bold Wikipedia semantic tokens in the canonical `theme.css`.
Existing `--cp-*` aliases map custom views to that same palette; do not introduce another theme.
No domain inference, model invocation, credentials or automatic approval belongs in the browser.
