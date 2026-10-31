from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from ekb.domain.execution import CheckOptions
from ekb.domain.models import (
    Candidate,
    ClassificationProperty,
    ConceptDetail,
    ConceptRelationshipKind,
    ConceptSource,
    ConceptVersion,
    Occurrence,
    Relationship,
    RelationshipKind,
    Scope,
    SemanticType,
    Usage,
    stable_id,
)
from ekb.domain.reconciliation import (
    EmbeddingResult,
    SemanticJudgment,
    Verification,
)
from ekb.domain.resolution import Finding, KnowledgeSnapshot
from ekb.domain.traceability import (
    ParentSelection,
    TraceabilitySelection,
    TraceabilityVerification,
)
from ekb.evaluation.provenance import evaluate_provenance, load_provenance_dataset
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.stages import _check_cache_key
from ekb.stages.enrichment import Check
from ekb.stages.issues import traceability_cardinality
from ekb.stages.provenance import (
    company_alignment,
    platform_alignment_check,
    sibling_comparison_check,
)


def concept(
    semantic_type: SemanticType,
    statement: str,
    *,
    source_key: str | None = None,
    source_id: UUID | None = None,
    platform: str | None = None,
) -> ConceptDetail:
    concept_id, version_id = uuid4(), uuid4()
    version = ConceptVersion(
        id=version_id,
        concept_id=concept_id,
        candidate=Candidate(
            statement=statement,
            excerpt=statement,
            semantic_type=semantic_type,
            source_key=source_key,
            scope=Scope(platform=platform),
        ),
    )
    occurrence = Occurrence(
        id=uuid4(),
        concept_version_id=version_id,
        segment_id=uuid4(),
        excerpt=statement,
        start=0,
        end=len(statement),
    )
    sources = (
        [
            ConceptSource(
                id=source_id,
                name="Source",
                location="https://example.test/source",
                version_ids=[version_id],
            )
        ]
        if source_id
        else []
    )
    return ConceptDetail(
        id=concept_id,
        versions=[version],
        occurrences=[occurrence],
        relationships=[],
        sources=sources,
    )


def context(
    concepts: list[ConceptDetail],
    *,
    prior_findings: tuple[Finding, ...] = (),
    snapshot_findings: tuple[Finding, ...] = (),
    max_candidates: int = 500,
) -> CheckContext:
    snapshot = KnowledgeSnapshot(
        revision="test",
        governance_revision="test",
        concepts=tuple(concepts),
        current_version_ids=frozenset(version.id for item in concepts for version in item.versions),
        findings=snapshot_findings,
        active_finding_ids=frozenset(finding.id for finding in snapshot_findings),
    )
    return CheckContext(
        run_id=uuid4(),
        input_revision="test",
        snapshot=snapshot,
        options=CheckOptions(max_candidates=max_candidates),
        prior_findings=prior_findings,
    )


def relationship(
    source: ConceptDetail,
    target: ConceptDetail,
    kind: ConceptRelationshipKind,
) -> Relationship:
    return Relationship(
        id=uuid4(),
        from_concept_id=source.id,
        to_concept_id=target.id,
        kind=kind,
        occurrence_id=source.occurrences[0].id,
    )


def proposed_parent(child: ConceptDetail, parent: ConceptDetail) -> Finding:
    return Finding(
        id=uuid4(),
        check_id="platform_alignment",
        check_version="test",
        input_revision="test",
        action="relationship",
        concept_ids=(child.id, parent.id),
        version_ids=(child.versions[0].id, parent.versions[0].id),
        occurrence_ids=(child.occurrences[0].id, parent.occurrences[0].id),
        relationship=RelationshipKind.REFINES,
        scope=child.versions[0].candidate.scope,
        confidence=0.9,
        explanation="The control specializes the broader requirement.",
    )


async def test_company_alignment_uses_explicit_source_adoption_and_allows_leaf_company_intent():
    source_id = stable_id("manifest", "nist")
    adopted = concept(
        SemanticType.STRATEGIC_REQUIREMENT,
        "Use NIST as the control baseline.",
        source_key="nist",
    )
    leaf_goal = concept(SemanticType.STRATEGIC_GOAL, "Grow responsibly.")
    requirement = concept(
        SemanticType.TECHNICAL_REQUIREMENT,
        "Deny network traffic by default.",
        source_id=source_id,
    )

    result = await company_alignment(context([adopted, leaf_goal, requirement]))

    assert len(result.findings) == 1
    assert result.findings[0].concept_ids == (requirement.id, adopted.id)
    assert result.findings[0].relationship is RelationshipKind.REFINES
    assert result.statistics == {
        "company_total": 2,
        "company_with_children": 1,
        "company_without_children": 1,
        "agnostic_total": 1,
        "agnostic_parented": 1,
        "agnostic_orphaned": 0,
    }
    repeated = await company_alignment(
        context(
            [adopted, leaf_goal, requirement],
            snapshot_findings=result.findings,
        )
    )
    assert tuple(item.id for item in repeated.findings) == tuple(
        item.id for item in result.findings
    )


async def test_company_alignment_preserves_existing_requirement_domain_company_chain():
    adopted = concept(SemanticType.STRATEGIC_REQUIREMENT, "Use NIST.")
    domain = concept(SemanticType.ARCHITECTURE_DOMAIN, "Network security")
    requirement = concept(
        SemanticType.TECHNICAL_REQUIREMENT, "Separate publicly accessible components."
    )
    requirement.relationships.append(
        relationship(requirement, domain, ClassificationProperty.IN_DOMAIN)
    )
    domain.relationships.append(relationship(domain, adopted, RelationshipKind.SUPPORTS))

    result = await company_alignment(context([adopted, domain, requirement]))

    assert not result.findings
    assert result.statistics["agnostic_parented"] == 2
    assert result.statistics["agnostic_orphaned"] == 0


class ReplayStore:
    def __init__(self):
        self.entries = {}
        self.workflow = self

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def semantic_cache(self, keys):
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def save_semantic_cache(self, entries):
        self.entries.update(entries)

    async def commit(self):
        pass


class Embeddings:
    fingerprint = "embedding-test"
    model_id = "embedding-test"

    async def embed(self, texts):
        return EmbeddingResult(
            vectors=tuple((1.0, float(index + 1) / 100) for index, _ in enumerate(texts)),
            input_tokens=len(texts),
            calls=1,
            usage_complete=True,
            cost=0,
        )


class Mapper:
    fingerprint = "mapper-test"

    async def map(self, child, parents):
        parent = parents[0]
        return TraceabilityVerification(
            selection=TraceabilitySelection(
                links=(
                    ParentSelection(
                        parent_concept_id=parent.concept_id,
                        confidence=0.91,
                        rationale="The control implements the broader requirement.",
                        child_excerpt=child.excerpt,
                        parent_excerpt=parent.excerpt,
                    ),
                )
            ),
            method="test",
            model_id="test-model",
            model_calls=1,
            usage=Usage(input_tokens=10, output_tokens=5, latency_ms=2, cost=0),
        )


class EvaluationMapper(Mapper):
    fingerprint = "evaluation-mapper-test"

    async def map(self, child, parents):
        if "Network Security Perimeter" in child.statement and not any(
            "Deny network communications traffic by default" in parent.statement
            for parent in parents
        ):
            return TraceabilityVerification(
                selection=TraceabilitySelection(
                    unresolved_reason="No supplied parent governs this control."
                ),
                method="test",
            )
        return await super().map(child, parents)


class FailingNegativeMapper(EvaluationMapper):
    async def map(self, child, parents):
        if "Network Security Perimeter" in child.statement and not any(
            "Deny network communications traffic by default" in parent.statement
            for parent in parents
        ):
            raise ConnectionError("Synthetic provider failure")
        return await super().map(child, parents)


async def test_platform_alignment_maps_controls_to_bounded_agnostic_parents():
    parent = concept(SemanticType.TECHNICAL_REQUIREMENT, "Deny network traffic by default.")
    control = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Use a network security perimeter for resource isolation.",
        platform="Azure",
    )
    replay = ReplayStore()
    check = platform_alignment_check(
        cast(UnitOfWorkFactory, replay), Mapper(), Embeddings(), concurrency=2
    )

    result = await check.evaluate(context([parent, control]))
    repeated = await check.evaluate(context([parent, control], snapshot_findings=result.findings))

    assert len(result.findings) == 1
    assert tuple(item.id for item in repeated.findings) == tuple(
        item.id for item in result.findings
    )
    assert result.findings[0].concept_ids == (control.id, parent.id)
    assert result.findings[0].relationship is RelationshipKind.REFINES
    assert result.statistics["platform_controls_parented"] == 1
    assert result.statistics["platform_controls_orphaned"] == 0


class Verifier:
    fingerprint = "verifier-test"

    async def verify(self, old, new):
        return Verification(
            judgment=SemanticJudgment(
                outcome="more_specific_guidance",
                confidence=0.88,
                rationale="The controls are adjacent implementations under one requirement.",
                old_excerpt=old.excerpt,
                new_excerpt=new.excerpt,
            ),
            method="test",
            model_id="test-model",
            model_calls=1,
            usage=Usage(input_tokens=10, output_tokens=5, latency_ms=2, cost=0),
        )


async def test_sibling_comparison_consumes_platform_findings_from_same_stage():
    parent = concept(SemanticType.TECHNICAL_REQUIREMENT, "Protect managed network boundaries.")
    left = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Use private endpoints.",
        platform="Azure",
    )
    right = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Use network security perimeters.",
        platform="Azure",
    )
    check = sibling_comparison_check(
        cast(UnitOfWorkFactory, ReplayStore()), Verifier(), Embeddings(), concurrency=2
    )
    ctx = context(
        [parent, left, right],
        prior_findings=(proposed_parent(left, parent), proposed_parent(right, parent)),
    )

    result = await check.evaluate(ctx)

    assert len(result.findings) == 1
    assert result.findings[0].relationship is RelationshipKind.MAPS_TO
    assert result.statistics["sibling_pairs_adjacent"] == 1
    assert result.statistics["sibling_pairs_compared"] == 1


async def test_traceability_cardinality_enforces_only_required_parent_directions():
    company = concept(SemanticType.STRATEGIC_GOAL, "Operate securely.")
    domain = concept(SemanticType.ARCHITECTURE_DOMAIN, "Network security")
    requirement = concept(SemanticType.TECHNICAL_REQUIREMENT, "Protect managed network boundaries.")
    control = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Use network security perimeters.",
        platform="Azure",
    )
    requirement.relationships.append(
        relationship(requirement, domain, ClassificationProperty.IN_DOMAIN)
    )
    domain.relationships.append(relationship(domain, company, RelationshipKind.SUPPORTS))

    missing = await traceability_cardinality(context([company, domain, requirement, control]))
    assert [item.issue_type for item in missing.issues] == ["unparented_platform_control"]

    complete = await traceability_cardinality(
        context(
            [company, domain, requirement, control],
            prior_findings=(proposed_parent(control, requirement),),
        )
    )
    assert not complete.issues
    assert complete.statistics["platform_controls_orphaned"] == 0


async def test_provenance_dataset_runs_through_pydantic_evals(tmp_path):
    dataset_path = Path("evals") / "provenance-v1.json"
    raw, dataset = load_provenance_dataset(dataset_path)
    assert raw
    assert len(dataset.cases) == 12

    report = await evaluate_provenance(
        dataset_path,
        tmp_path / "report.json",
        EvaluationMapper(),
        split="all",
        history_dir=tmp_path / "history",
    )

    assert report["passed"] is True
    assert report["precision"] == 1
    assert report["recall"] == 1
    assert report["exact_cases"] == 12


async def test_provenance_evaluation_fails_when_negative_case_execution_fails(tmp_path):
    report = await evaluate_provenance(
        Path("evals") / "provenance-v1.json",
        tmp_path / "report.json",
        FailingNegativeMapper(),
        split="test",
        history_dir=tmp_path / "history",
    )

    assert report["passed"] is False
    assert "no-defensible-parent" in report["failures"]


def test_ordered_check_cache_depends_on_prior_output_revisions():
    async def evaluate(_context: CheckContext) -> CheckResult:
        return CheckResult()

    check = Check("downstream", evaluate)
    options = CheckOptions()

    first = _check_cache_key(check, options, "input", ["upstream-a"])
    second = _check_cache_key(check, options, "input", ["upstream-b"])

    assert first != second
