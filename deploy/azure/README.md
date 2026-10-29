# Azure deployment foundation

`container-apps.env.example` lists placeholder settings for separate web and worker processes.

- Use the root image and `config\azure.yaml` against pre-provisioned PostgreSQL and Blob Storage.
- Configure real model capabilities, managed-identity permissions and optional Monitor export.
- Run `ekb init` explicitly before serving the migrated schema.

No resource provisioning, network isolation or enterprise authentication is implemented here.
See `docs\architecture.md` and `docs\usage.md` before deploying beyond a private environment.
