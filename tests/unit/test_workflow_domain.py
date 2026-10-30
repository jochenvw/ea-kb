from datetime import date
from uuid import uuid4

import pytest

from ekb.domain.execution import CheckOptions, PipelineConfiguration, ProcessorSpec, StageOptions
from ekb.domain.models import (
    Candidate,
    ConceptDetail,
    ConceptVersion,
    Occurrence,
    Scope,
    SemanticType,
)
from ekb.domain.resolution import Deviation, KnowledgeSnapshot, overlaps, resolve_alias
from ekb.orchestration.registry import StageRegistry
from ekb.ports.workflow import CheckContext
from ekb.stages.enrichment import Check
from ekb.stages.issues import (
    conflicting_requirements,
    deviation_issues,
    provenance_gaps,
)


def context(scopes=None):
    scopes = scopes or (Scope(), Scope())
    concepts = []
    for text, scope in zip(
        ("Operators must enable audit logging.", "Operators must not enable audit logging."),
        scopes,
        strict=True,
    ):
        cid, vid, sid = uuid4(), uuid4(), uuid4()
        version = ConceptVersion(
            id=vid,
            concept_id=cid,
            candidate=Candidate(
                semantic_type=SemanticType.PLATFORM_REQUIREMENT
                if scope.platform
                else SemanticType.TECHNICAL_REQUIREMENT,
                statement=text,
                excerpt=text,
                scope=scope,
            ),
        )
        occurrence = Occurrence(
            id=uuid4(), concept_version_id=vid, segment_id=sid, excerpt=text, start=0, end=len(text)
        )
        concepts.append(
            ConceptDetail(id=cid, versions=[version], occurrences=[occurrence], relationships=[])
        )
    snapshot = KnowledgeSnapshot(
        revision="test",
        governance_revision="test",
        concepts=tuple(concepts),
        current_version_ids=frozenset(v.id for c in concepts for v in c.versions),
    )
    return CheckContext(
        run_id=uuid4(), input_revision="test", snapshot=snapshot, options=CheckOptions()
    )


async def test_conflict_requires_overlapping_scope_and_explicit_opposition():
    result = await conflicting_requirements(context())
    assert len(result.issues) == 1
    assert result.issues[0].issue_type == "conflicting_requirements"
    disjoint = context((Scope(platform="Azure"), Scope(platform="AWS")))
    assert not (await conflicting_requirements(disjoint)).issues
    assert not overlaps(
        Scope(effective_to=date(2020, 1, 1)), Scope(effective_from=date(2021, 1, 1))
    )


def test_excessive_deviations_count_distinct_solutions_not_renewals():
    ctx = context()
    cid = ctx.snapshot.concepts[0].id
    occurrence = ctx.snapshot.concepts[0].occurrences[0].id
    records = tuple(
        Deviation(
            id=uuid4(),
            concept_id=cid,
            solution=f"solution-{i}",
            scope=Scope(),
            approved=True,
            effective_from=date(2020, 1, 1),
            evidence_occurrence_id=occurrence,
        )
        for i in range(11)
    )
    assert len(deviation_issues(ctx, records).issues) == 1
    renewal = records[0].model_copy(update={"id": uuid4()})
    assert not deviation_issues(ctx, records[:10] + (renewal,)).issues
    expired = records[-1].model_copy(update={"effective_until": date(2020, 2, 1)})
    assert not deviation_issues(ctx, records[:10] + (expired,)).issues


async def test_missing_source_evidence_is_an_issue_even_without_current_occurrences():
    ctx = context().model_copy(update={"check_version": "replacement-v2"})
    broken = ctx.snapshot.concepts[0].model_copy(update={"occurrences": []})
    ctx = ctx.model_copy(
        update={"snapshot": ctx.snapshot.model_copy(update={"concepts": (broken,)})}
    )
    result = await provenance_gaps(ctx)
    assert result.issues[0].issue_type == "missing_provenance"
    assert result.issues[0].check_version == "replacement-v2"


async def test_tension_is_not_a_proven_hard_conflict():
    from ekb.stages.enrichment import semantic_duplicates

    ctx = context()
    concept = ctx.snapshot.concepts[1]
    version = concept.versions[0]
    changed = version.model_copy(
        update={
            "candidate": version.candidate.model_copy(
                update={"statement": "Operators must not enable verbose audit logging."}
            )
        }
    )
    snapshot = ctx.snapshot.model_copy(
        update={
            "concepts": (
                ctx.snapshot.concepts[0],
                concept.model_copy(update={"versions": [changed]}),
            )
        }
    )
    ctx = ctx.model_copy(update={"snapshot": snapshot})
    proposed = await semantic_duplicates(ctx)
    assert proposed.findings
    snapshot = snapshot.model_copy(
        update={
            "findings": proposed.findings,
            "active_finding_ids": frozenset(f.id for f in proposed.findings),
        }
    )
    detected = await conflicting_requirements(ctx.model_copy(update={"snapshot": snapshot}))
    assert {i.issue_type for i in detected.issues} == {"potential_trade_off"}


def test_alias_cycles_are_rejected_instead_of_resolved_arbitrarily():
    a, b, c = uuid4(), uuid4(), uuid4()
    assert resolve_alias(a, {a: b, b: c}) == (a, b, c)
    with pytest.raises(ValueError, match="Cyclic"):
        resolve_alias(a, {a: b, b: a})


def test_registry_rejects_unknown_checks_and_dependency_cycles():
    class Stage:
        def __init__(self, name, dependencies):
            self.spec = ProcessorSpec(id=name, name=name, version="1", dependencies=dependencies)
            self.checks = ()

        async def execute(self, context):
            raise AssertionError("Validation must happen before execution")

    registry = StageRegistry()
    registry.register(Stage("one", ("two",)))
    registry.register(Stage("two", ("one",)))
    with pytest.raises(ValueError, match="Cyclic"):
        registry.order(("one", "two"))
    clean = StageRegistry()
    clean.register(Stage("one", ()))
    with pytest.raises(ValueError, match="Unknown check"):
        clean.validate(
            PipelineConfiguration(stages={"one": StageOptions(checks={"unknown": CheckOptions()})})
        )


def test_plugin_configuration_has_its_own_typed_schema():
    from ekb.domain.models import Model
    from ekb.ports.workflow import CheckResult

    class Configuration(Model):
        minimum: int

    async def evaluate(ctx):
        Configuration.model_validate(ctx.options.parameters)
        return CheckResult()

    check = Check("custom", evaluate, configuration_type=Configuration)
    check.validate_configuration(CheckOptions(parameters={"minimum": 3}))
    with pytest.raises(ValueError):
        check.validate_configuration(CheckOptions(parameters={"unexpected": 3}))


def test_candidate_retrieval_bounds_comparisons_not_only_successful_matches(monkeypatch):
    import ekb.stages.enrichment as module

    ctx = context()
    prototype = ctx.snapshot.concepts[0]
    concepts = []
    for i in range(20):
        cid, vid = uuid4(), uuid4()
        text = f"Operators must protect exactly endpoint number {i} with audited security controls."
        version = prototype.versions[0].model_copy(
            update={
                "id": vid,
                "concept_id": cid,
                "candidate": prototype.versions[0].candidate.model_copy(update={"statement": text}),
            }
        )
        concepts.append(prototype.model_copy(update={"id": cid, "versions": [version]}))
    snapshot = ctx.snapshot.model_copy(
        update={
            "concepts": tuple(concepts),
            "current_version_ids": frozenset(c.versions[0].id for c in concepts),
        }
    )
    comparisons = 0
    original = module.SequenceMatcher

    def counting_matcher(*args, **kwargs):
        nonlocal comparisons
        comparisons += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "SequenceMatcher", counting_matcher)
    list(
        module.pairs(
            ctx.model_copy(
                update={
                    "snapshot": snapshot,
                    "options": CheckOptions(max_candidates=3, similarity_threshold=1.0),
                }
            )
        )
    )
    assert 0 < comparisons <= 3
