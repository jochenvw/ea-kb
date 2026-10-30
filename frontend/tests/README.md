# Browser regressions

`npm test` runs Playwright against an owned Vite server; install Chromium with `npx playwright install chromium`.
API fixtures are synthetic and local, so this suite needs no database, corpus, model, credentials or Azure.
Tests cover hierarchy, evidence escaping, structured versions, scope/authority separation, review
guards, server cursors, error recovery, desktop navigation and themes. No review or scan is submitted.
Architect journeys cover source curation guidance, selected-version evidence/adoption, explicit graph
selection and review tabs. The supported target is PC browsers; do not add mobile-browser checks.
Curation guidance never represents saved approval.
Reference/work regressions cover source-bound versions, graph recentering/proposal styles,
backlog versus ledger, foreign-key navigation, terminal pages and searchable log tails.
Ontology regressions cover exact definitions, linked scopes, search, escaping, deep links and themes.
Python integration tests cover the actual FastAPI routes, authentication and persisted services.
Real-corpus visual smoke checks use the built application served by `ekb web`.
