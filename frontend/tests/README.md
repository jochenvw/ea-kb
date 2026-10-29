# Browser regressions

`npm test` runs Playwright against an owned Vite server; install Chromium with `npx playwright install chromium`.
API fixtures are synthetic and local, so this suite needs no database, corpus, model, credentials or Azure.
Tests cover hierarchy, evidence escaping, structured versions, scope/authority separation, review
guards, server cursors, error recovery, responsive navigation and themes. No review or scan is submitted.
Reference/work regressions cover source-bound versions, graph recentering/proposal styles,
backlog versus ledger, foreign-key navigation, terminal pages and searchable log tails.
Python integration tests cover the actual FastAPI routes, authentication and persisted services.
Real-corpus visual smoke checks use the built application served by `ekb web`.
