import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from opentelemetry import metrics
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData


class JSONLMetricExporter(MetricExporter):
    def __init__(self, path: Path, max_bytes: int, backups: int):
        super().__init__()
        self.handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10000,
        **kwargs: Any,
    ) -> MetricExportResult:
        payload = json.dumps(json.loads(metrics_data.to_json()), separators=(",", ":"))
        self.handler.handle(
            logging.LogRecord("ekb.metrics", logging.INFO, "", 0, payload, (), None)
        )
        return MetricExportResult.SUCCESS

    def shutdown(self, timeout_millis: float = 30000, **kwargs: Any) -> None:
        self.handler.close()

    def force_flush(self, timeout_millis: float = 10000) -> bool:
        self.handler.flush()
        return True


def count(name: str, value: int = 1, **attributes: str | bool | int) -> None:
    metrics.get_meter("ekb").create_counter(name).add(value, attributes)
