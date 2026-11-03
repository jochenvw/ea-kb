# Agent and CI walkthrough

Assess the real reference app from an agent (MCP), CI and the browser. Every step uses the local
EKB store and the ingested public corpus; nothing calls a model or the cloud, and nothing approves
on your behalf. Background: `usage.md`, "Verified solution assessment".

## 1. Prepare (once)

```powershell
uv run ekb baselines register examples\reference-app\baseline.json        # prints pending_approvals
uv run ekb architectures register examples\reference-app\design\restricted.yaml `
  examples\reference-app\design\restricted.json                           # prints the architecture ID
```

The baseline binds ingested security benchmark concept versions; if your corpus differs, rebind
them with `ekb concepts search` (see `examples\reference-app\README.md`).

## 2. Fail: authority not established

```powershell
uv run ekb assess ARCHITECTURE_ID examples\reference-app\main.bicep `
  --baseline-concept BASELINE_CONCEPT --baseline-version BASELINE_VERSION `
  --parameters examples\reference-app\parameters\restricted.parameters.json --require conforms
```

Exit code 1. Design is `accepted` and implementation `passed` for all three requirements, but each
finding is `unknown` because no person has adopted the baseline yet. Unknown never passes.

## 3. Decide (human)

Review each `pending_approvals` finding in `/review` or with
`uv run ekb reconcile decide FINDING_ID accepted --actor YOU --rationale "..."`. This is an
enterprise decision; do not automate it.

## 4. Fail: real violation, then pass

Re-run step 2 with `parameters\restricted-no-ddos.parameters.json`: the DDoS requirement becomes
`violated` (implementation `failed` on `Microsoft.Network/virtualNetworks/refapp-vnet`), exit 1.
Re-run with `parameters\restricted.parameters.json`: every applicable requirement `conforms`, exit 0.
Before step 3 the same DDoS check already reports implementation `failed`, but stays `unknown`.
`tests\integration\test_reference_app.py` verifies these outcomes offline against a disposable schema.

## 5. Agent (MCP)

`.vscode\mcp.json` (Copilot in VS Code) and `.mcp.json` (Copilot CLI, after trusting the folder)
start `uv run ekb mcp`. Example prompt:

> Use the ekb `assess_solution` tool to assess architecture ARCHITECTURE_ID against baseline
> BASELINE_CONCEPT / BASELINE_VERSION with the template `examples/reference-app/main.json` and
> parameters `{"profile": "restricted"}`. List each finding's disposition and remediation.

Inspect raw tool calls and results with `npx @modelcontextprotocol/inspector uv run ekb mcp`
(Tools → `assess_solution`).

## 6. CI and browser

`examples\ci\ekb-assess.yml` runs step 2 in GitHub Actions, fails unless the solution conforms and
writes the findings table to the job summary (`scripts\assessment_summary.py`).
Every CLI, MCP, API and CI run is retained: open `http://127.0.0.1:8000/solutions/ARCHITECTURE_ID`
(**Latest verified assessment**) and `/portfolio` after `uv run ekb web`.
