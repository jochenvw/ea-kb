# Workspace presentation

`app.tsx` groups Work, Reference and Operations; feature files own their API-backed pages.
`sources.tsx` groups benchmark editions, then drills into their documents and source-bound versions.
`network.tsx` navigates evidence/relationship graphs; source evidence remains document-specific.
`review.tsx` separates the active backlog from the read-only ledger in `reference.tsx`.
`reference.tsx` also provides bounded, linked table previews and safe operational log tails.
`ontology.tsx` reads canonical definitions/IRIs from `/api/ontology`; shared badges link to its terms.
`main.tsx` mounts the app; `styles.css` defines responsive layout.
`knowledge.tsx` filters editions or exact documents, links matching versions and highlights retained quotes.
Goals/guidance and platform implementations have separate filters and graph lanes; mappings show evidence.
Use real API records, abort stale reads, disclose query limits and preserve unknown values.
Publisher maturity, semantic versioning and scoped enterprise authority must stay distinct.
Native review forms retain server-side Basic authentication, origin checks and immutable auditing.
Use `components\ui` and the Bold Wikipedia semantic tokens in the canonical `theme.css`.
Existing `--cp-*` aliases map custom views to that same palette; do not introduce another theme.
No domain inference, model invocation, credentials or automatic approval belongs in the browser.
