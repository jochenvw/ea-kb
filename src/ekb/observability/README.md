# Application telemetry

`__init__.py` configures structlog and OpenTelemetry once per process.

- Logs, spans and `metrics.py` counters export rotating JSONL under the configured data directory.
- Writers use `telemetry\<process-id>\` so concurrent CLI/UI processes never rotate each other's files.
- Size/backup limits apply per process; retain or clean old process directories operationally.
- Azure settings optionally add Monitor trace/log/metric exporters to the same instrumentation.
- Application exception spans record type/status, not sensitive messages or source payloads.

Use the shared `log` and `tracer`; flush on runtime teardown.
Debug model payloads belong to the inference adapter, not normal telemetry.
Privacy regression coverage is in `tests\unit\test_adapters.py`.
