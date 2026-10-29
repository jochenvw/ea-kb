from __future__ import annotations

from ekb.domain.benchmarks import NormalizedBenchmarkDocument
from ekb.domain.models import (
    Candidate,
    ConceptKind,
    Extraction,
    InferenceResult,
    Scope,
    fingerprint,
)

BENCHMARK_EXTRACTOR_VERSION = fingerprint(implementation="benchmark-requirements-v1")


def benchmark_extraction(
    document: NormalizedBenchmarkDocument,
) -> tuple[InferenceResult, list[str], tuple[str, ...]]:
    candidates = []
    keys = []
    unmaterialized = []
    for control in document.controls:
        statements = [
            (
                control.control_id,
                control.security_principle or control.title,
                control.principle_evidence.excerpt if control.principle_evidence else control.title,
            ),
            *(
                (child.control_id, f"{child.title}\n\n{child.text}", child.evidence.excerpt)
                for child in control.subcontrols
            ),
        ]
        for key, statement, excerpt in statements:
            if len(statement) > 4000:
                # Preserve the complete record rather than truncating normative qualifiers.
                unmaterialized.append(key)
                continue
            candidates.append(
                Candidate(
                    kind=ConceptKind.REQUIREMENT,
                    statement=statement,
                    excerpt=excerpt,
                    scope=Scope(platform="Azure"),
                    source_key=f"{control.edition}:{key}",
                    confidence=1.0,
                    uncertainty="Deterministic publisher recommendation; not enterprise approval.",
                )
            )
            keys.append(key)
    return (
        InferenceResult(
            extraction=Extraction(candidates=candidates),
            model_id="deterministic-microsoft-benchmark",
            prompt_version=BENCHMARK_EXTRACTOR_VERSION,
        ),
        keys,
        tuple(unmaterialized),
    )
