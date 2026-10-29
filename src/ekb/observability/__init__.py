from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode
from structlog.typing import EventDict

from ekb.config import Settings


class ApplicationTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any):
        with trace.get_tracer("ekb").start_as_current_span(
            name, record_exception=False, set_status_on_exception=False, **kwargs
        ) as span:
            try:
                yield span
            except BaseException as error:
                span.set_attribute("error.type", type(error).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise


tracer = ApplicationTracer()
log = structlog.get_logger("ekb")
_provider: TracerProvider | None = None
_log_provider: Any = None


class JSONLSpanExporter(SpanExporter):
    """Uses the SDK exporter contract; rotation is shared with the log implementation."""

    def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
        self.handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def export(self, spans: Any) -> SpanExportResult:
        for span in spans:
            record = logging.LogRecord("ekb.trace", logging.INFO, "", 0, span.to_json(), (), None)
            record.msg = json.dumps(json.loads(str(record.msg)), separators=(",", ":"))
            self.handler.handle(record)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.handler.close()


def correlate(_: object, __: str, event: EventDict) -> EventDict:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event["trace_id"] = f"{context.trace_id:032x}"
        event["span_id"] = f"{context.span_id:016x}"
    return event


def configure(settings: Settings) -> None:
    global _provider, _log_provider
    if _provider is not None:
        return
    directory = settings.data_dir / "telemetry"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "logs.jsonl",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backups,
        encoding="utf-8",
    )
    logger = logging.getLogger("ekb")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            correlate,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    provider = TracerProvider(resource=Resource.create({"service.name": "ekb"}))
    provider.add_span_processor(
        BatchSpanProcessor(
            JSONLSpanExporter(
                directory / "traces.jsonl", settings.log_max_bytes, settings.log_backups
            )
        )
    )
    if settings.azure_monitor_connection_string.get_secret_value():
        from azure.monitor.opentelemetry.exporter import (
            AzureMonitorLogExporter,
            AzureMonitorTraceExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        provider.add_span_processor(
            BatchSpanProcessor(
                AzureMonitorTraceExporter(
                    connection_string=settings.azure_monitor_connection_string.get_secret_value()
                )
            )
        )
        _log_provider = LoggerProvider(resource=Resource.create({"service.name": "ekb"}))
        _log_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                AzureMonitorLogExporter(
                    connection_string=settings.azure_monitor_connection_string.get_secret_value()
                )
            )
        )
        logger.addHandler(LoggingHandler(logger_provider=_log_provider))
    trace.set_tracer_provider(provider)
    _provider = provider


def flush() -> None:
    if _provider:
        _provider.force_flush()
    if _log_provider:
        _log_provider.force_flush()
