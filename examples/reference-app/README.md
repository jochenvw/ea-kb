# Reference app

A real assessment input set for `ekb baselines register`, `ekb architectures register` and
`ekb assess` (walkthrough: `docs\usage.md`, "Verified solution assessment").

- `baseline.json`: `BaselinePolicy` binding three ingested Azure security benchmark concept
  versions to ARM checks: NS-5.1 network-layer DDoS protection (refines NIST SC-5), Azure service
  audit logs, and NS-2.1 disabling public access behind private endpoints (private workloads). IDs are `uuid5` values derived from the
  ingested corpus; if your workspace differs, find replacements with `ekb concepts search`.
- `design\*.json` / `design\*.yaml`: structured design and architecture manifest per profile.
- `main.bicep` and its compiled `main.json` (`az bicep build --file main.bicep --stdout`), plus
  `parameters\*` profiles. The template is never deployed by EKB.

The template and parameters are shared with `tests\integration\test_reference_app.py`.
