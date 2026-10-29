# Application telemetry

`__init__.py` configures structlog and OpenTelemetry once per process.

- Logs and spans are correlated, rotating JSONL under the configured data directory.
- Azure settings optionally add Monitor trace/log exporters to the same instrumentation.
- Application exception spans record type/status, not sensitive messages or source payloads.

Use the shared `log` and `tracer`; flush on runtime teardown.
Debug model payloads belong to the inference adapter, not normal telemetry.
Privacy regression coverage is in `tests\unit\test_adapters.py`.
