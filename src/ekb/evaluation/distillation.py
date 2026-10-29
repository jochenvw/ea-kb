from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ekb.adapters.inference import INSTRUCTIONS
from ekb.domain.grounding import validate_evidence
from ekb.domain.models import Candidate, Extraction, Scope, Segment, content_hash, now, stable_id
from ekb.evaluation.dataset import extraction_cases, load_dataset
from ekb.ports import InferencePort


def _publish(output: Path, rows: list[dict], metadata: dict) -> None:
    created = []
    try:
        with output.open("x", encoding="utf-8") as labels:
            created.append(output)
            path = output.with_suffix(".metadata.json")
            with path.open("x", encoding="utf-8") as provenance:
                created.append(path)
                labels.write("\n".join(json.dumps(r) for r in rows) + "\n")
                provenance.write(json.dumps(metadata, indent=2))
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


async def export_training(
    dataset_path: Path,
    output: Path,
    teacher: InferencePort | None = None,
    max_cases: int = 32,
) -> dict:
    if await asyncio.to_thread(output.exists) or await asyncio.to_thread(
        output.with_suffix(".metadata.json").exists
    ):
        raise ValueError("Create a new dataset version; do not overwrite existing teacher labels")
    dataset = await asyncio.to_thread(load_dataset, dataset_path)
    cases = extraction_cases(dataset, "train")
    if len(cases) > max_cases:
        raise ValueError("Training case budget exceeded before any teacher calls")
    rows = []
    tokens = 0
    for case in cases:
        segment = Segment(
            id=stable_id("distill", case.id),
            revision_id=stable_id("distill", case.source),
            ordinal=0,
            text=case.text,
            location=case.location,
        )
        if teacher:
            result = await teacher.extract(segment)
            validate_evidence(result.extraction, segment)
            extraction = result.extraction
            tokens += result.usage.input_tokens + result.usage.output_tokens
        else:
            extraction = Extraction(
                candidates=[
                    Candidate(
                        kind=e.kind,
                        statement=e.excerpt,
                        excerpt=e.excerpt,
                        modality=e.modality,
                        scope=Scope.model_validate(e.scope),
                    )
                    for e in case.expected
                ]
            )
        rows.append(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": INSTRUCTIONS
                        + "\nSchema:\n"
                        + json.dumps(Extraction.model_json_schema()),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "source_reference": str(segment.id),
                                "text": segment.text,
                            }
                        ),
                    },
                    {"role": "assistant", "content": extraction.model_dump_json()},
                ]
            }
        )
    metadata = {
        "recorded_at": now().isoformat(),
        "dataset_sha256": content_hash(await asyncio.to_thread(dataset_path.read_bytes)),
        "teacher": teacher.capabilities.model_id if teacher else dataset.teacher_model,
        "generation": "configured_teacher" if teacher else dataset.generation,
        "review_status": "teacher_only",
        "split": "train",
        "cases": len(rows),
        "source_ids": sorted({c.source for c in cases}),
        "sources": [s.model_dump(mode="json") for s in dataset.sources if s.split == "train"],
        "case_ids": [c.id for c in cases],
        "teacher_tokens": tokens,
        "limitations": "Training-ready examples only; no fine-tuning job or student quality claim.",
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(_publish, output, rows, metadata)
    except FileExistsError:
        raise ValueError("Create a new dataset version; do not overwrite teacher labels") from None
    return metadata
