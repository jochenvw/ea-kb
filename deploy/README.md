# Deployment entry points

- `local` explains host development and Docker Compose.
- `azure` describes the existing-resource environment contract, not infrastructure provisioning.
- Both modes use the same package and shared PostgreSQL/artifact-store semantics.

Start with `Dockerfile`, `compose.yaml` and the selected subfolder README.
Keep databases and historical originals together when backing up or changing storage.
