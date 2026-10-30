# Azure deployment foundation

`container-apps.env.example` lists placeholder settings for separate web and worker processes.

- Use the root image and `config\azure.yaml` against pre-provisioned PostgreSQL and Blob Storage.
- Configure real model capabilities, managed-identity permissions and optional Monitor export.
- Run `ekb init` explicitly before serving; pre-v1 development uses current ORM metadata.

No resource provisioning, network isolation or enterprise authentication is implemented here.
The web/API rejects non-loopback access and forwarded-proxy headers; it is not remotely usable or
ready for Azure deployment until an Entra authentication boundary is implemented and verified.
See `docs\architecture.md` and `docs\usage.md` before deploying beyond a private environment.
