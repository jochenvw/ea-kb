# 0014 Verified assessment and derived portfolio views

Status: accepted (application 0.1.0)

- One `SolutionAssessmentService` serves CLI, API, MCP and CI. It resolves an approved,
  machine-readable baseline independently of the author's checklist, plans each requirement against
  structured design JSON and verifies implementation through the `ImplementationAnalyzer` port.
  The first adapter evaluates compiled ARM/Bicep templates; unsupported checks stay unknown.
- Design and implementation outcomes stay separate. Unknown authority, applicability or evidence
  never passes. The superseded declared-evidence assessment was removed.
- Exception risk and change impact are on-demand projections over the latest registered revision
  per solution. Rates use the applicable population; review signals never score, renew or weaken a
  standard. Change impact matches by concept identity; proposals and publisher releases are never
  confirmed. Drafts are exported, not sent.
- Every assessment report is retained immutably in `solution_assessments`, keyed by its
  fingerprint, so CLI, MCP and CI runs are inspectable on the solution page. Portfolio trends still
  recompute earlier dates rather than storing snapshots.

Evidence: `tests\unit\test_assessment.py`, `tests\unit\test_portfolio.py`,
`tests\integration\test_reference_app.py`, `tests\integration\test_portfolio_service.py`,
`evals\architecture-assessment-v2.json`.