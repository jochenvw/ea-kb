import json

from ekb.adapters.persistence.postgres.inspection import preview
from ekb.observability.inspection import read_events


def test_record_previews_bound_content_and_redact_credential_fields():
    result = preview(
        {
            "body": "x" * 5000,
            "values": list(range(30)),
            "metadata": {"authorization": "do-not-serve", "publisher": "Example"},
        }
    )
    assert len(result["body"]) < 4100 and result["body"].endswith("[preview truncated]")
    assert result["values"][:20] == list(range(20))
    assert result["values"][-1] == "[additional items omitted]"
    assert result["metadata"] == {"authorization": "[redacted]", "publisher": "Example"}


def test_log_tails_are_bounded_ordered_and_ignore_nonfinite_diagnostics(tmp_path):
    directory = tmp_path / "telemetry" / "123"
    directory.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "event": "transient_retry",
                "timestamp": f"2026-01-01T10:0{i}:00Z",
                "attempt": float("nan") if i == 2 else i,
                "error_type": "TransportError",
            }
        )
        for i in range(3)
    ]
    (directory / "logs.jsonl").write_text("x" * 70000 + "\n" + "\n".join(lines))
    result = read_events(tmp_path, limit=2)
    assert result.truncated and result.files_read == 1
    assert [event.timestamp.minute for event in result.events] == [2, 1]
    assert result.events[0].fields == {"error_type": "TransportError"}
    assert read_events(tmp_path / "absent").events == []
