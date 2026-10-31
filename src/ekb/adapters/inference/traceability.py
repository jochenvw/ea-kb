from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ValidationError

from ekb.adapters.inference import OpenAIInference
from ekb.adapters.retry import retry
from ekb.config import Settings
from ekb.domain.models import Usage, fingerprint
from ekb.domain.traceability import (
    TraceabilityAssertion,
    TraceabilitySelection,
    TraceabilityVerification,
)
from ekb.observability import tracer

INSTRUCTIONS = (
    Path(__file__)
    .with_name("skills")
    .joinpath("control_traceability.md")
    .read_text(encoding="utf-8")
)


def _input(assertion: TraceabilityAssertion) -> dict[str, object]:
    return {
        "concept_id": str(assertion.concept_id),
        "statement": assertion.statement,
        "excerpt": assertion.excerpt,
        "semantic_type": assertion.semantic_type,
        "scope": assertion.scope.model_dump(mode="json"),
    }


class OpenAITraceabilityMapper:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.chat = OpenAIInference(
            settings.model_copy(update={"model_id": settings.reconciliation_model_id}),
            client=client,
        )
        self.fingerprint = fingerprint(
            implementation="control-traceability-v1",
            model=settings.reconciliation_model_id,
            endpoint=settings.model_endpoint,
            instructions=INSTRUCTIONS,
            schema=TraceabilitySelection.model_json_schema(),
            structured=settings.model_structured_output,
            context=settings.model_context_tokens,
            output=settings.model_max_output_tokens,
            temperature=settings.model_temperature,
            token_parameter=settings.model_max_token_parameter,
        )

    async def map(
        self,
        child: TraceabilityAssertion,
        parents: tuple[TraceabilityAssertion, ...],
    ) -> TraceabilityVerification:
        settings = self.chat.settings
        messages = [
            {
                "role": "system",
                "content": (
                    INSTRUCTIONS + "\n" + json.dumps(TraceabilitySelection.model_json_schema())
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "child": _input(child),
                        "candidate_parents": [_input(parent) for parent in parents],
                    }
                ),
            },
        ]
        parent_by_id = {parent.concept_id: parent for parent in parents}
        started = time.monotonic()
        calls = tokens_in = tokens_out = failures = metered_responses = 0
        selection = TraceabilitySelection(
            unresolved_reason="No valid grounded traceability selection."
        )
        method = "model_failed"
        status: Literal["completed", "failed", "context_limit"] = "failed"
        for _ in range(settings.model_retries + 1):
            if (
                sum(len(message["content"].encode()) for message in messages)
                + settings.model_max_output_tokens
                > settings.model_context_tokens
            ):
                method = "context_limit"
                status = "context_limit"
                break

            async def complete() -> tuple[str, int, int]:
                nonlocal calls
                calls += 1
                return await self.chat._complete(messages, TraceabilitySelection)

            try:
                with tracer.start_as_current_span("traceability.control_mapping"):
                    text, used_in, used_out = await retry(complete)
                tokens_in += used_in
                tokens_out += used_out
                if used_in > 0 and used_out > 0:
                    metered_responses += 1
                candidate = TraceabilitySelection.model_validate_json(text)
                for link in candidate.links:
                    parent = parent_by_id.get(link.parent_concept_id)
                    if parent is None:
                        raise ValueError("Traceability output referenced an unknown parent")
                    if link.child_excerpt not in child.excerpt:
                        raise ValueError("Child evidence is not grounded in the supplied excerpt")
                    if link.parent_excerpt not in parent.excerpt:
                        raise ValueError("Parent evidence is not grounded in the supplied excerpt")
                selection, method, status = candidate, "small_model", "completed"
                break
            except (ValidationError, ValueError):
                failures += 1
                messages.append(
                    {
                        "role": "user",
                        "content": "Invalid IDs, schema, or evidence. Return grounded JSON only.",
                    }
                )
            except httpx.HTTPError:
                break
        return TraceabilityVerification(
            selection=selection,
            method=method,
            status=status,
            model_id=self.chat.capabilities.model_id,
            model_calls=calls,
            usage=Usage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                model_calls=calls,
                validation_failures=failures,
                latency_ms=(time.monotonic() - started) * 1000,
                unmetered_model_calls=calls - metered_responses,
                cost=(
                    (
                        tokens_in * settings.model_input_price
                        + tokens_out * settings.model_output_price
                    )
                    / 1_000_000
                    if settings.model_input_price is not None
                    and settings.model_output_price is not None
                    and calls > 0
                    and metered_responses == calls
                    else None
                ),
            ),
        )

    async def close(self) -> None:
        await self.chat.close()
