# Browser presentation

`frontend` at the repository root owns React/TypeScript and official shadcn/ui components.
`static\ui` contains committed production HTML/CSS/JavaScript, source fingerprints and licenses.
`static\theme.css` holds the Bold Wikipedia light/dark palette, typography, radius and shadows.
FastAPI serves the built shell and existing JSON services; production needs no Node.js process.
The UI groups Work (backlog/pipeline), Reference (sources/knowledge/graph/ledger) and Operations.
Source details show evidenced versions; table/log inspection is bounded and strictly read-only.
Knowledge and proposals use server cursors; evidence and identifiers sit behind drill-downs.
Governance forms retain authenticated server handlers, explicit rationale and append-only auditing.
See `frontend\README.md`, `tests\integration\test_interfaces.py` and `docs\usage.md`.
