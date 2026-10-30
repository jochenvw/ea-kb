import ast
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ekb.domain.grounding import GroundingError, validate_evidence
from ekb.domain.models import (
    Candidate,
    Extraction,
    Modality,
    Scope,
    Segment,
    SemanticType,
    SemanticVersion,
    content_hash,
    fingerprint,
    stable_id,
)


@pytest.mark.parametrize(
    "layer", ["domain", "ports", "pipeline", "services", "orchestration", "stages"]
)
def test_inner_layers_do_not_import_infrastructure(layer):
    forbidden = (
        "ekb.adapters",
        "ekb.interfaces",
        "ekb.runtime",
        "sqlalchemy",
        "fastapi",
        "httpx",
        "azure",
        "openai",
        "textual",
        "agent_framework",
        "pydantic_evals",
    )
    if layer in {"domain", "ports"}:
        forbidden += (
            "ekb.config",
            "ekb.observability",
            "ekb.services",
            "ekb.pipeline",
            "ekb.orchestration",
            "ekb.stages",
            "ekb.evaluation",
        )
    root = Path(__file__).resolve().parents[2] / "src" / "ekb" / layer
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                assert not any(
                    module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden
                ), f"{path.relative_to(root)}:{node.lineno} imports {module}"


@given(st.binary())
def test_hash_and_identity_are_deterministic(content):
    assert content_hash(content) == content_hash(content)
    assert stable_id(content_hash(content)) == stable_id(content_hash(content))
    assert len(content_hash(content)) == 64


def test_processing_fingerprint_tracks_configuration_not_source_identity():
    assert fingerprint(content="x", parser="1") != fingerprint(content="x", parser="2")
    assert fingerprint(a=1, b=2) == fingerprint(b=2, a=1)


def test_scope_and_modality_validation():
    with pytest.raises(ValidationError):
        Scope.model_validate({"effective_from": "2026-02-01", "effective_to": "2026-01-01"})
    with pytest.raises(ValidationError):
        Candidate(
            semantic_type=SemanticType.STRATEGIC_GOAL,
            statement="Reduce costs",
            excerpt="Reduce costs",
            modality=Modality.MANDATED,
        )
    with pytest.raises(ValidationError):
        Candidate(
            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
            statement="x",
            excerpt="x",
            confidence=1.1,
        )
    assert (
        Candidate(
            semantic_type=SemanticType.TECHNICAL_REQUIREMENT, statement="x", excerpt="x"
        ).modality
        == Modality.UNSPECIFIED
    )


@pytest.mark.parametrize(
    "value",
    ["1.0", "01.2.3", "-1.0.0", "1.2.3-01", "1.2.3-a..b", "1.2.3+", "1.2.3-draft_1"],
)
def test_invalid_semver(value):
    with pytest.raises(ValueError):
        SemanticVersion.parse(value)


def test_semver():
    assert str(SemanticVersion.parse("2.12.3")) == "2.12.3"


def test_semver_prerelease_precedence_and_build_identity():
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    versions = [SemanticVersion.parse(value) for value in ordered]
    assert sorted(reversed(versions)) == versions
    for a, b in zip(versions, versions[1:], strict=False):
        assert a.compare_precedence(b) == -1
        assert b.compare_precedence(a) == 1
    build = SemanticVersion.parse("1.0.0-preview.2+build.007")
    assert str(build) == "1.0.0-preview.2+build.007"
    assert build.compare_precedence(SemanticVersion.parse("1.0.0-preview.2+other")) == 0
    assert build != SemanticVersion.parse("1.0.0-preview.2+other")


def test_semver_validates_direct_construction():
    with pytest.raises(ValidationError):
        SemanticVersion(prerelease=("01",))
    with pytest.raises(ValidationError):
        SemanticVersion(build=("",))


def test_exact_evidence_grounding_preserves_negation():
    segment = Segment(
        id=uuid4(),
        revision_id=uuid4(),
        ordinal=0,
        text="Operators must not disable alarms except during approved maintenance.",
    )
    valid = Candidate(
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        statement=segment.text,
        excerpt=segment.text,
    )
    validate_evidence(Extraction(candidates=[valid]), segment)
    with pytest.raises(GroundingError):
        validate_evidence(
            Extraction(
                candidates=[valid.model_copy(update={"excerpt": "Operators must disable alarms"})]
            ),
            segment,
        )
