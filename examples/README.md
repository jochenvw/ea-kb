# Examples

Real, runnable inputs for the solution-assessment flow. They bind to concept versions ingested
from the configured public corpora, never to synthetic requirements, and nothing here approves
anything: registration only proposes canonical adoption for a human decision.

- `reference-app`: an Azure web app baseline, two designs and one Bicep template.
- `ci`: a GitHub Actions workflow that gates a solution repository on `ekb assess`.

MCP client configuration is committed at `.vscode\mcp.json` (VS Code) and `.mcp.json` (Copilot CLI);
`docs\agent-walkthrough.md` covers agent, CI and browser use end to end.

Deterministic test variations live in `tests\fixtures\reference-app`; see `docs\usage.md`.
