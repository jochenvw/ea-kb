# VS Code workspace settings

`mcp.json` registers the read-only EKB MCP server (`uv run ekb mcp`, stdio) for Copilot in VS Code.
It uses the configured workspace database and needs no credentials in this file.
Copilot CLI reads the repository-root `.mcp.json` instead. Walkthrough: `docs\agent-walkthrough.md`.
