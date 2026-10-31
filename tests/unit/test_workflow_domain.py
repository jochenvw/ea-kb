from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from ekb.domain.execution import CheckOptions, PipelineConfiguration, ProcessorSpec, StageOptions
from ekb.domain.models import (
    Candidate,
    ClassificationProperty,
    ConceptDetail,
    ConceptVersion,
    Occurrence,
    Relationship,
    RelationshipKind,
    Scope,
    SemanticType,
    Usage,
)
from ekb.domain.reconciliation import (
    CachedEmbedding,
    CachedVerification,
    EmbeddingResult,
    SemanticAssertion,
    SemanticJudgment,
    SemanticOutcome,
    Verification,
    VerificationStatus,
)
from ekb.domain.resolution import (
    Decision,
    Deviation,
    Finding,
    KnowledgeSnapshot,
    active_aliases,
    alias_edges,
    overlaps,
    resolve_alias,
)
from ekb.orchestration.registry import StageRegistry
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext
from ekb.stages.enrichment import Check, lineage
from ekb.stages.issues import (
    conflicting_requirements,
    deviation_issues,
    provenance_gaps,
)
from ekb.stages.semantic import _embeddings, _verification, _verification_key, semantic_check


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


async def test_uncertain_relation_only_reports_a_real_provenance_gap():
    ctx = context()
    concept = ctx.snapshot.concepts[0]
    finding = Finding(
        id=uuid4(),
        check_id="semantic",
        check_version="1",
        input_revision="test",
        action="relationship",
        concept_ids=(concept.id,),
        occurrence_ids=(concept.occurrences[0].id,),
        relationship=RelationshipKind.MAPS_TO,
        confidence=0.5,
        explanation="The relation needs review.",
        uncertainty="The target scope is unknown.",
    )
    snapshot = ctx.snapshot.model_copy(
        update={"findings": (finding,), "active_finding_ids": frozenset({finding.id})}
    )
    assert not (await provenance_gaps(ctx.model_copy(update={"snapshot": snapshot}))).issues
    snapshot = snapshot.model_copy(
        update={"findings": (finding.model_copy(update={"occurrence_ids": ()}),)}
    )
    result = await provenance_gaps(ctx.model_copy(update={"snapshot": snapshot}))
    assert {issue.issue_type for issue in result.issues} == {"missing_relationship_provenance"}


async def test_lineage_records_ontology_classification_relationships():
    ctx = context()
    source, target = ctx.snapshot.concepts
    relationship = Relationship(
        id=uuid4(),
        from_concept_id=source.id,
        to_concept_id=target.id,
        kind=ClassificationProperty.IN_DOMAIN,
        occurrence_id=source.occurrences[0].id,
        status="configured",
    )
    source = source.model_copy(update={"relationships": [relationship]})
    snapshot = ctx.snapshot.model_copy(update={"concepts": (source, target)})

    result = await lineage(ctx.model_copy(update={"snapshot": snapshot}))

    assert len(result.findings) == 1
    assert result.findings[0].relationship == ClassificationProperty.IN_DOMAIN


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


def test_aliases_follow_effective_and_reversed_decisions_without_a_projection():
    source, target, replacement = uuid4(), uuid4(), uuid4()
    finding = Finding(
        id=uuid4(),
        check_id="review",
        check_version="1",
        input_revision="test",
        action="alias",
        concept_ids=(source, target),
        confidence=1.0,
        explanation="Evidence-backed identity proposal.",
    )
    accepted = Decision(
        finding_id=finding.id,
        outcome="accepted",
        actor="reviewer",
        rationale="Reviewed.",
        recorded_at=datetime(2020, 1, 1, tzinfo=UTC),
        effective_at=date(2020, 1, 1),
        effective_until=date(2020, 12, 31),
    )
    reversed_decision = Decision(
        finding_id=finding.id,
        outcome="reversed",
        actor="reviewer",
        rationale="Withdrawn.",
        recorded_at=datetime(2022, 1, 1, tzinfo=UTC),
        effective_at=date(2022, 1, 1),
        previous_id=accepted.id,
    )
    next_finding = finding.model_copy(update={"id": uuid4(), "concept_ids": (source, replacement)})
    next_decision = Decision(
        finding_id=next_finding.id,
        outcome="accepted",
        actor="reviewer",
        rationale="New reviewed target.",
        recorded_at=datetime(2023, 1, 1, tzinfo=UTC),
        effective_at=date(2023, 1, 1),
    )
    snapshot = context().snapshot.model_copy(
        update={
            "findings": (finding, next_finding),
            "decisions": (accepted, reversed_decision, next_decision),
        }
    )
    assert active_aliases(snapshot, date(2019, 1, 1)) == {}
    assert active_aliases(snapshot, date(2020, 6, 1)) == {source: target}
    assert active_aliases(snapshot, date(2021, 6, 1)) == {}
    assert active_aliases(snapshot, date(2022, 6, 1)) == {}
    assert active_aliases(snapshot, date(2023, 6, 1)) == {source: replacement}
    with pytest.raises(ValueError, match="missing finding"):
        active_aliases(snapshot.model_copy(update={"findings": ()}), date(2020, 6, 1))


def test_derived_aliases_reject_competing_targets_and_cycles():
    a, b, c = uuid4(), uuid4(), uuid4()
    finding = Finding(
        id=uuid4(),
        check_id="review",
        check_version="1",
        input_revision="test",
        action="alias",
        concept_ids=(a, b),
        confidence=1.0,
        explanation="Evidence-backed identity proposal.",
    )
    competing = finding.model_copy(update={"id": uuid4(), "concept_ids": (a, c)})
    cycle = finding.model_copy(update={"id": uuid4(), "concept_ids": (b, a)})
    with pytest.raises(ValueError, match="Competing"):
        alias_edges((finding, competing))
    with pytest.raises(ValueError, match="Cyclic"):
        alias_edges((finding, cycle))
    with pytest.raises(ValueError, match="two different identities"):
        alias_edges((finding.model_copy(update={"concept_ids": (a,)}),))


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


class ReplayStore:
    def __init__(self, ctx=None):
        self.entries = {}
        self.active = 0
        self.unavailable = False
        self.workflow = self.knowledge = self
        self.occurrences = (
            {concept.id: concept.occurrences for concept in ctx.snapshot.concepts} if ctx else {}
        )

    def __call__(self):
        return self

    async def __aenter__(self):
        self.active += 1
        return self

    async def __aexit__(self, *args):
        self.active -= 1

    async def commit(self):
        pass

    async def provenance(self, concept_id):
        return [
            SimpleNamespace(occurrence=occurrence) for occurrence in self.occurrences[concept_id]
        ]

    async def semantic_cache(self, keys):
        if self.unavailable:
            raise RuntimeError("Replay store unavailable")
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def save_semantic_cache(self, entries):
        self.entries.update(entries)


class ReplayEmbeddings:
    model_id = "synthetic-embeddings"

    def __init__(self, store, *, cost: float | None = 0.25, usage_complete: bool | None = True):
        self.store = store
        self.fingerprint = "embedding-provider-v1"
        self.vector: tuple[float, ...] = (1.0, 0.0)
        self.response = None
        self.cost = cost
        self.usage_complete = usage_complete
        self.batches = []
        self.unavailable = False

    async def embed(self, texts):
        assert self.store.active == 0
        if self.unavailable:
            raise RuntimeError("Embedding provider unavailable")
        self.batches.append(tuple(texts))
        return EmbeddingResult(
            vectors=self.response
            if self.response is not None
            else tuple(self.vector for _ in texts),
            input_tokens=len(texts),
            calls=1,
            cost=self.cost,
            usage_complete=self.usage_complete,
        )


class ReplayVerifier:
    def __init__(self, store, *, costs=(0.5,)):
        self.store = store
        self.fingerprint = "verification-provider-v1"
        self.costs = costs
        self.calls = 0
        self.method = "small_model"
        self.status: VerificationStatus = "completed"
        self.outcome: SemanticOutcome = "equivalent"
        self.ungrounded = False
        self.invalid = False
        self.unavailable = False

    async def verify(self, old, new):
        assert self.store.active == 0
        if self.unavailable:
            raise RuntimeError("Verification provider unavailable")
        cost = self.costs[self.calls % len(self.costs)]
        self.calls += 1
        judgment = SemanticJudgment(
            outcome=self.outcome,
            confidence=0.9,
            rationale="Grounded synthetic judgment.",
            old_excerpt="invented" if self.ungrounded else old.excerpt,
            new_excerpt=new.excerpt,
        )
        if self.invalid:
            judgment = judgment.model_copy(update={"confidence": 2.0})
        return Verification(
            method=self.method,
            status=self.status,
            model_id="synthetic-verifier",
            judgment=judgment,
            model_calls=1,
            usage=Usage(input_tokens=8, output_tokens=3, latency_ms=4, cost=cost),
        )


def assertion(text):
    return SemanticAssertion(
        occurrence_id=uuid4(),
        statement=text,
        excerpt=text,
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        scope=Scope(platform="example"),
    )


async def replay_verification(store, provider, old, new):
    async with store() as uow:
        cache = await uow.workflow.semantic_cache([_verification_key(provider, old, new)])
    return await _verification(cast(UnitOfWorkFactory, store), provider, old, new, cache)


async def test_changed_semantic_check_replays_without_calls_or_rebilling():
    ctx = context()
    store = ReplayStore(ctx)
    embeddings, verifier = ReplayEmbeddings(store), ReplayVerifier(store)
    check = semantic_check(cast(UnitOfWorkFactory, store), verifier, embeddings)
    first = await check.evaluate(ctx)
    assert first.embedding_calls == first.model_calls == 1
    assert first.usage.input_tokens == 10 and first.usage.cost == pytest.approx(0.75)
    assert len(first.findings) == 1

    embeddings.unavailable = verifier.unavailable = True
    replay = await check.evaluate(ctx.model_copy(update={"check_version": "new-version"}))
    assert replay.model_calls == replay.embedding_calls == 0
    assert replay.usage.input_tokens == replay.usage.output_tokens == 0
    assert replay.usage.latency_ms == 0 and replay.usage.cost is None
    assert replay.findings[0].action == first.findings[0].action
    assert replay.findings[0].check_version == "new-version"


async def test_embedding_replay_keys_include_provider_and_exact_text():
    store = ReplayStore()
    provider = ReplayEmbeddings(store)
    database = cast(UnitOfWorkFactory, store)
    _, calls, tokens, costs = await _embeddings(database, provider, ["First", "Second"])
    assert (calls, tokens, costs) == (1, 2, [0.25])
    _, calls, _, _ = await _embeddings(database, provider, ["Second", "First"])
    assert calls == 0
    _, calls, _, _ = await _embeddings(database, provider, ["First", "Second "])
    assert calls == 1 and provider.batches[-1] == ("Second ",)
    provider.fingerprint = "embedding-provider-v2"
    _, calls, _, _ = await _embeddings(database, provider, ["First", "Second"])
    assert calls == 1 and provider.batches[-1] == ("First", "Second")


@pytest.mark.parametrize(
    "vector",
    [(), (0.0, 0.0), (float("nan"), 1.0), (float("inf"), 1.0), (1.0,) * 16385],
    ids=["empty", "zero", "nan", "infinity", "oversized"],
)
async def test_invalid_cached_embeddings_are_recomputed_and_repaired(vector):
    store = ReplayStore()
    provider = ReplayEmbeddings(store)
    database = cast(UnitOfWorkFactory, store)
    await _embeddings(database, provider, ["First", "Second"])
    key = next(iter(store.entries))
    store.entries[key] = CachedEmbedding.model_construct(kind="embedding", vector=vector)
    vectors, calls, _, _ = await _embeddings(database, provider, ["First", "Second"])
    assert calls == 1 and provider.batches[-1] == ("First",)
    assert vectors == {"First": (1.0, 0.0), "Second": (1.0, 0.0)}
    assert store.entries[key].vector == (1.0, 0.0)


async def test_inconsistent_cached_dimensions_and_provider_drift_are_repaired():
    store = ReplayStore()
    provider = ReplayEmbeddings(store)
    database = cast(UnitOfWorkFactory, store)
    await _embeddings(database, provider, ["First", "Second"])
    key = next(iter(store.entries))
    store.entries[key] = CachedEmbedding(vector=(1.0, 0.0, 0.0))
    _, calls, _, _ = await _embeddings(database, provider, ["First", "Second"])
    assert calls == 1 and provider.batches[-1] == ("First", "Second")

    provider.vector = (1.0, 0.0, 0.0)
    vectors, calls, tokens, costs = await _embeddings(database, provider, ["First", "Third"])
    assert calls == 2 and tokens == 2 and costs == [0.25, 0.25]
    assert provider.batches[-2:] == [("Third",), ("First",)]
    assert {len(vector) for vector in vectors.values()} == {3}


@pytest.mark.parametrize(
    "vectors",
    [
        ((0.0, 0.0), (1.0, 0.0)),
        ((float("nan"), 1.0), (1.0, 0.0)),
        ((1.0,) * 16385, (1.0,) * 16385),
        ((1.0, 0.0), (1.0,)),
        ((1.0, 0.0),),
    ],
    ids=["zero", "nan", "oversized", "dimensions", "count"],
)
async def test_invalid_provider_embeddings_are_not_cached(vectors):
    store = ReplayStore()
    provider = ReplayEmbeddings(store)
    provider.response = vectors
    with pytest.raises(ValueError, match="Embedding provider"):
        await _embeddings(cast(UnitOfWorkFactory, store), provider, ["First", "Second"])
    assert not store.entries


async def test_verifier_replay_keys_include_ordered_semantic_inputs_and_provider():
    store = ReplayStore()
    provider = ReplayVerifier(store)
    old, new = assertion("Protect endpoints."), assertion("Protect external endpoints.")
    first = await replay_verification(store, provider, old, new)
    assert first.model_calls == 1 and first.usage.cost == 0.5
    same_input = old.model_copy(update={"occurrence_id": uuid4()})
    replay = await replay_verification(store, provider, same_input, new)
    assert replay.model_calls == 0 and replay.usage.cost is None
    assert provider.calls == 1

    changes = [
        (old.model_copy(update={"statement": old.statement + " Carefully."}), new),
        (old.model_copy(update={"excerpt": old.excerpt + " Extra evidence."}), new),
        (old.model_copy(update={"semantic_type": SemanticType.STRATEGIC_REQUIREMENT}), new),
        (old.model_copy(update={"scope": Scope(platform="another-platform")}), new),
        (new, old),
    ]
    for changed_old, changed_new in changes:
        result = await replay_verification(store, provider, changed_old, changed_new)
        assert result.model_calls == 1
    assert provider.calls == 1 + len(changes)
    provider.fingerprint = "verification-provider-v2"
    assert (await replay_verification(store, provider, old, new)).model_calls == 1


async def test_third_party_method_and_successful_unknown_replay_without_calls():
    store = ReplayStore()
    old, new = assertion("Protect endpoints."), assertion("Protect external endpoints.")

    class ThirdPartyVerifier:
        fingerprint = "independent-verifier-v1"
        calls = 0
        unavailable = False

        async def verify(self, old, new):
            assert store.active == 0
            if self.unavailable:
                raise RuntimeError("Verifier is unavailable")
            self.calls += 1
            return Verification(
                method="independent.semantic-v1",
                model_calls=1,
                usage=Usage(input_tokens=8, cost=0.5),
                judgment=SemanticJudgment(
                    outcome="unknown",
                    confidence=0.1,
                    rationale="Evidence does not establish a relationship.",
                    old_excerpt=old.excerpt,
                    new_excerpt=new.excerpt,
                ),
            )

    provider = ThirdPartyVerifier()
    first = await replay_verification(store, provider, old, new)
    provider.unavailable = True
    replay = await replay_verification(store, provider, old, new)
    assert first.completed and first.model_calls == 1 and first.usage.cost == 0.5
    assert replay.completed and replay.method == first.method == "independent.semantic-v1"
    assert replay.judgment.outcome == "unknown"
    assert replay.model_calls == 0 and replay.usage.cost is None and provider.calls == 1


@pytest.mark.parametrize("corruption", ["invalid", "ungrounded", "failed-method", "failed-status"])
async def test_corrupt_cached_judgments_are_revalidated_and_regrounded(corruption):
    store = ReplayStore()
    provider = ReplayVerifier(store)
    old, new = assertion("Protect endpoints."), assertion("Protect external endpoints.")
    await replay_verification(store, provider, old, new)
    key, entry = next(iter(store.entries.items()))
    assert isinstance(entry, CachedVerification)
    if corruption in {"invalid", "ungrounded"}:
        change = {"confidence": 2.0} if corruption == "invalid" else {"old_excerpt": "invented"}
        entry = entry.model_copy(update={"judgment": entry.judgment.model_copy(update=change)})
    elif corruption == "failed-method":
        entry = entry.model_copy(update={"method": "model_failed"})
    else:
        entry = entry.model_copy(update={"status": "failed"})
    store.entries[key] = entry
    result = await replay_verification(store, provider, old, new)
    assert result.model_calls == 1 and provider.calls == 2
    assert store.entries[key].judgment == result.judgment


@pytest.mark.parametrize(
    "failure",
    [
        "legacy-failed",
        "legacy-context",
        "provider-failed",
        "provider-context",
        "ungrounded",
        "invalid",
    ],
)
async def test_failed_or_ungrounded_verification_is_never_cached(failure):
    store = ReplayStore()
    provider = ReplayVerifier(store)
    provider.method = {
        "legacy-failed": "model_failed",
        "legacy-context": "context_limit",
    }.get(failure, "independent.semantic-v1")
    if failure == "provider-failed":
        provider.status = "failed"
    elif failure == "provider-context":
        provider.status = "context_limit"
    provider.ungrounded = failure == "ungrounded"
    provider.invalid = failure == "invalid"
    old, new = assertion("Protect endpoints."), assertion("Protect external endpoints.")
    with pytest.raises(ValueError):
        await replay_verification(store, provider, old, new)
    assert not store.entries
    provider.method = "independent.semantic-v1"
    provider.status = "completed"
    provider.ungrounded = provider.invalid = False
    assert (await replay_verification(store, provider, old, new)).model_calls == 1
    assert (await replay_verification(store, provider, old, new)).model_calls == 0


async def test_unavailable_replay_store_fails_before_any_provider_call():
    store = ReplayStore()
    store.unavailable = True
    embeddings, verifier = ReplayEmbeddings(store), ReplayVerifier(store)
    with pytest.raises(RuntimeError, match="Replay store unavailable"):
        await _embeddings(cast(UnitOfWorkFactory, store), embeddings, ["First"])
    with pytest.raises(RuntimeError, match="Replay store unavailable"):
        await replay_verification(store, verifier, assertion("First."), assertion("Second."))
    assert not embeddings.batches and verifier.calls == 0


@pytest.mark.parametrize("costs", [(0.5, None, 1.0), (None, 0.5, 1.0), (0.5, 1.0, None)])
async def test_semantic_aggregation_never_reports_partial_known_cost(costs):
    ctx = context()
    original = ctx.snapshot.concepts[0]
    concept_id, version_id, segment_id = uuid4(), uuid4(), uuid4()
    statement = "Operators must enable full audit logging."
    version = original.versions[0].model_copy(
        update={
            "id": version_id,
            "concept_id": concept_id,
            "candidate": original.versions[0].candidate.model_copy(
                update={"statement": statement, "excerpt": statement}
            ),
        }
    )
    occurrence = Occurrence(
        id=uuid4(),
        concept_version_id=version_id,
        segment_id=segment_id,
        excerpt=statement,
        start=0,
        end=len(statement),
    )
    third = original.model_copy(
        update={"id": concept_id, "versions": [version], "occurrences": [occurrence]}
    )
    snapshot = ctx.snapshot.model_copy(
        update={
            "concepts": (*ctx.snapshot.concepts, third),
            "current_version_ids": ctx.snapshot.current_version_ids | {version_id},
        }
    )
    ctx = ctx.model_copy(update={"snapshot": snapshot})
    store = ReplayStore(ctx)
    verifier = ReplayVerifier(store, costs=costs)
    result = await semantic_check(cast(UnitOfWorkFactory, store), verifier, None).evaluate(ctx)
    assert result.model_calls == 3 and result.usage.cost is None


async def test_unpriced_embedding_makes_combined_semantic_cost_unknown():
    ctx = context()
    store = ReplayStore(ctx)
    result = await semantic_check(
        cast(UnitOfWorkFactory, store),
        ReplayVerifier(store),
        ReplayEmbeddings(store, cost=None),
    ).evaluate(ctx)
    assert result.model_calls == result.embedding_calls == 1
    assert result.usage.cost is None


@pytest.mark.parametrize("usage_complete", [False, None])
async def test_unmetered_embedding_makes_combined_semantic_cost_unknown(usage_complete):
    ctx = context()
    store = ReplayStore(ctx)
    result = await semantic_check(
        cast(UnitOfWorkFactory, store),
        ReplayVerifier(store),
        ReplayEmbeddings(store, usage_complete=usage_complete),
    ).evaluate(ctx)
    assert result.model_calls == result.embedding_calls == 1
    assert result.usage.cost is None
