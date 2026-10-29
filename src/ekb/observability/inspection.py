import json
import math
import re
from datetime import datetime
from itertools import islice
from pathlib import Path

from ekb.domain.inspection import LogEvent, LogPage

EVENTS = {
    "source_failed",
    "source_skipped",
    "segment_skipped",
    "segment_replayed",
    "pipeline_retry",
    "transient_retry",
    "model_validation_failed",
    "debug_artifact",
    "pipeline_check_done",
    "web_scan_failed",
}
FIELDS = {
    "source_id",
    "segment_id",
    "run_id",
    "task_id",
    "trace_id",
    "span_id",
    "stage_id",
    "check_id",
    "version",
    "attempt",
    "error_type",
    "model_calls",
    "embedding_calls",
    "input_tokens",
    "output_tokens",
}


def read_events(data_dir: Path, limit: int = 100) -> LogPage:
    root = data_dir / "telemetry"
    if not root.exists() or root.is_symlink() or root.is_junction():
        return LogPage(events=[], files_read=0, truncated=False)
    candidates = []
    for path in islice(root.glob("*/logs.jsonl*"), 256):
        if (
            path.parent.name.isdecimal()
            and re.fullmatch(r"logs\.jsonl(?:\.\d+)?", path.name)
            and not any(p.is_symlink() or p.is_junction() for p in (path, path.parent))
        ):
            try:
                candidates.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue
    events = []
    truncated = len(candidates) >= 16
    read = 0
    for _, path in sorted(candidates, reverse=True)[:16]:
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 65536))
                content = stream.read(65536)
                if size > 65536:
                    content = content.partition(b"\n")[2]
                    truncated = True
                lines = content.splitlines()
        except FileNotFoundError:
            continue
        read += 1
        for line in lines:
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("event") not in EVENTS:
                    continue
                timestamp = datetime.fromisoformat(event["timestamp"])
                if timestamp.tzinfo is None:
                    continue
                fields = {
                    key: value
                    for key in FIELDS
                    if isinstance(value := event.get(key), int)
                    or isinstance(value, float)
                    and math.isfinite(value)
                    or isinstance(value, str)
                    and re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", value)
                }
                events.append(
                    LogEvent(
                        timestamp=timestamp,
                        event=event["event"],
                        process=path.parent.name,
                        fields=fields,
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue
    events.sort(key=lambda event: event.timestamp, reverse=True)
    return LogPage(
        events=events[:limit], files_read=read, truncated=truncated or len(events) > limit
    )
