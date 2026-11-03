# CI example

`ekb-assess.yml` is a GitHub Actions workflow to copy into a solution repository. It runs
`ekb assess --require conforms` (exit 1 unless every applicable requirement conforms; unknown never
passes), writes `scripts\assessment_summary.py` Markdown to the job summary and uploads the JSON.
It needs a reachable EKB store (`EKB_DATABASE_URL` secret) plus the architecture and baseline IDs
as repository variables. `.bicep` input needs the Azure CLI, which GitHub-hosted runners include.
Steps and expected results: `docs\agent-walkthrough.md`.
