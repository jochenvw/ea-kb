from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from ekb.adapters.inference.traceability import (
    OpenAITraceabilityMapper,
    _explicit_mechanism_supports,
)
from ekb.adapters.inference.traceability import (
    _input as traceability_input,
)
from ekb.config import Settings
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
    FrameworkReference,
    ParentSelection,
    TraceabilityAssertion,
    TraceabilitySelection,
    TraceabilityVerification,
    standardized_mechanism_supports,
)
from ekb.evaluation.provenance import evaluate_provenance, load_provenance_dataset
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.stages import _check_cache_key
from ekb.stages.enrichment import Check
from ekb.stages.issues import traceability_cardinality
from ekb.stages.provenance import (
    ControlMappingOptions,
    _lexical_tokens,
    _parent_candidate_limit,
    _rank_parents,
    _standardized_parent_candidates,
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


async def test_traceability_mapper_batches_and_explicitly_assesses_every_candidate(monkeypatch):
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Restrict network traffic using platform rules.",
        excerpt="Restrict network traffic using platform rules.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
        scope=Scope(platform="Platform"),
    )
    parents = tuple(
        TraceabilityAssertion(
            concept_id=uuid4(),
            version_id=uuid4(),
            occurrence_id=uuid4(),
            statement=f"Parent requirement {index}.",
            excerpt=f"Parent requirement {index}.",
            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        )
        for index in range(21)
    )
    calls: list[int] = []

    async def complete(messages, schema):
        payload = json.loads(messages[-1]["content"])
        candidates = payload["candidate_parents"]
        calls.append(len(candidates))
        if payload.get("phase") == "adversarial_critique":
            return (
                json.dumps(
                    {
                        "assessments": [
                            {
                                "parent_concept_id": parent["concept_id"],
                                "verdict": "refines",
                                "confidence": 0.99,
                                "rationale": "The selected parent survives adversarial review.",
                                "child_excerpt": payload["child"]["excerpt"],
                                "parent_excerpt": parent["excerpt"],
                            }
                            for parent in candidates
                        ]
                    }
                ),
                10,
                5,
            )
        if "candidate_assessments" in payload:
            parent = candidates[0]
            return (
                json.dumps(
                    {
                        "links": [
                            {
                                "parent_concept_id": parent["concept_id"],
                                "confidence": 0.99,
                                "rationale": "This is the closest direct parent.",
                                "child_excerpt": payload["child"]["excerpt"],
                                "parent_excerpt": parent["excerpt"],
                            }
                        ]
                    }
                ),
                10,
                5,
            )
        return (
            json.dumps(
                {
                    "assessments": [
                        {
                            "parent_concept_id": parent["concept_id"],
                            "verdict": "refines" if index == 0 else "not_refines",
                            "confidence": 0.95 if index == 0 else 0.9,
                            "rationale": "The child directly implements this requirement.",
                            "child_excerpt": payload["child"]["excerpt"],
                            "parent_excerpt": parent["excerpt"],
                        }
                        for index, parent in enumerate(candidates)
                    ],
                }
            ),
            10,
            5,
        )

    mapper = OpenAITraceabilityMapper(
        Settings(model_id="test", reconciliation_model_id="test", model_retries=0)
    )
    monkeypatch.setattr(mapper.chat, "_complete", complete)
    try:
        result = await mapper.map(child, parents)
    finally:
        await mapper.close()

    assert calls == [10, 10, 1, 3, 1]
    assert [link.parent_concept_id for link in result.selection.links] == [
        parents[0].concept_id,
    ]
    assert result.completed and result.method == "small_model_batched"
    assert result.model_calls == result.usage.model_calls == 5
    assert result.usage.input_tokens == 50 and result.usage.output_tokens == 25


def test_declared_framework_references_prioritize_matching_parent_control_ids():
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Use a platform perimeter for resource isolation.",
        excerpt="Use a platform perimeter for resource isolation.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
        scope=Scope(platform="Platform"),
        framework_references=(
            FrameworkReference(
                namespace="nist-sp-800-53",
                revision="5",
                control_id="sc-7.5",
                evidence_segment_id=uuid4(),
                evidence_excerpt="NIST SP 800-53 Rev.5: SC-7(5)",
            ),
        ),
    )
    referenced = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Deny network traffic by default.",
        excerpt="Deny network traffic by default.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        source_key="sc-7.5",
    )
    lexical_match = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Use a platform perimeter for isolation.",
        excerpt="Use a platform perimeter for isolation.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        source_key="ac-1",
    )

    ranked = _rank_parents(child, [lexical_match, referenced], {}, 1)

    assert ranked == (referenced,)


def test_lexical_retrieval_expands_equivalent_control_language():
    firewall = _lexical_tokens("Configure service firewalls to restrict access.")
    configuration = _lexical_tokens("Transition to enforced mode with baseline rules.")
    denial_of_service = _lexical_tokens("Mitigate DNS amplification attacks and floods.")

    assert {"authorization", "control", "flow"} <= firewall.keys()
    assert {"configuration", "settings"} <= configuration.keys()
    assert {"denial", "service"} <= denial_of_service.keys()


def test_explicit_standardized_mechanisms_survive_adversarial_review():
    assert _explicit_mechanism_supports(
        "Use Private Link for secure and private access to hosted services.",
        "Separate information flows logically or physically.",
    )
    assert _explicit_mechanism_supports(
        "Deploy a host-based IDS endpoint for host-level detection.",
        "Implement host-based monitoring mechanisms.",
    )
    assert not _explicit_mechanism_supports(
        "Use a private high-performance connection.",
        "Network integrity is protected through segmentation.",
    )


def test_platform_alignment_recognizes_bounded_standardized_mechanisms():
    supported = (
        (
            "Use network security group rules to allow or deny traffic.",
            "Enforce approved authorizations for controlling the flow of information.",
        ),
        (
            "Establish network segmentation boundaries between internal resources.",
            "Network integrity is protected through network segmentation.",
        ),
        (
            "Use managed identities and service principals for applications.",
            "Identity providers manage identities and credentials.",
        ),
        (
            "Run static application security testing before deployment.",
            "Require static code analysis to identify common flaws.",
        ),
        (
            "Scan for vulnerabilities and manage remediation.",
            "Monitor and scan for vulnerabilities in the system.",
        ),
        (
            "Enable DDoS protection against denial-of-service attacks.",
            "Protect against or limit the effects of denial-of-service attacks.",
        ),
        (
            "Collect diagnostic logs in a central workspace.",
            "Generate and centrally review audit log records.",
        ),
        (
            "Deploy endpoint detection and response and anti-malware.",
            "Implement malicious code protection mechanisms.",
        ),
        (
            "Use encryption to protect data in transit.",
            "Apply cryptographic mechanisms to preserve confidentiality.",
        ),
        (
            "Back up workloads to a recovery vault.",
            "Conduct backups and recover system data.",
        ),
    )
    assert all(standardized_mechanism_supports(child, parent) for child, parent in supported)
    assert not standardized_mechanism_supports(
        "Prepare network infrastructure for Azure Firewall deployment.",
        "Enforce information flow control policies.",
    )
    assert not standardized_mechanism_supports(
        "Manage DDoS Protection through the portal.",
        "Manage capacity and bandwidth to limit denial-of-service attacks.",
    )


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (
            "Control traffic to EC2 instances with security groups.",
            "Enforce information flow control using policy filters.",
        ),
        (
            "Create and accept VPC peering connections.",
            "Authorize internal connections of system components.",
        ),
        (
            "Use Azure VPN to create a secure site-to-site connection.",
            "Protect transmitted information.",
        ),
        (
            "Manage Azure Firewall rules through Firewall Manager with Firewall Policy objects.",
            "Centrally manage controls and related processes.",
        ),
        (
            "Deploy and configure Azure Firewall with comprehensive policies.",
            "Monitor and control communications at external managed interfaces.",
        ),
        (
            "Switch WAF to prevention mode to block malicious requests.",
            "Enforce information flow control using policy filters.",
        ),
        (
            "Implement private connectivity for cloud services.",
            "Separate information flows logically or physically.",
        ),
        (
            "Implement Network Security Perimeter to secure PaaS resources.",
            "Networks and environments are protected from unauthorized logical access.",
        ),
        (
            "Avoid assigning public IPs to VMs; use an Application Gateway front-end.",
            "Prohibit direct connection of the system to a public network.",
        ),
        (
            "Use Azure Policy initiatives to require NSG on every subnet.",
            "Implement restrictive configuration settings.",
        ),
        (
            "Start with Transition mode before moving to Enforced mode.",
            "Establish and implement restrictive configuration settings.",
        ),
        (
            "Add the resolver address to firewall and NSG allow lists.",
            "Enforce approved authorizations for controlling information flow.",
        ),
        (
            "Centralize security analysis with Azure Sentinel.",
            "Provide the capability to centrally review and analyze audit records.",
        ),
        (
            "Detect dangling DNS records to prevent subdomain takeover.",
            "Networks are monitored to find potentially adverse events.",
        ),
        (
            "Configure DNSSEC to protect DNS traffic from spoofing.",
            "Provide data origin authentication and integrity verification artifacts.",
        ),
        (
            "Use the Sender Policy Framework (SPF) to stop domain spoofing.",
            "Employ spam protection mechanisms against unsolicited messages.",
        ),
        (
            "Follow NIST SP 800-81 Secure DNS Deployment Guide best practices.",
            "Apply systems security and privacy engineering principles.",
        ),
        (
            "Generate high-quality alerts using native threat detection.",
            "Generate internal security alerts and advisories.",
        ),
        (
            "Apply naming conventions and mandatory resource tags.",
            "Configuration management practices are established and applied.",
        ),
        (
            "Protect privileged access, administrative accounts, and privileged workstations.",
            "Apply least privilege to privileged accounts.",
        ),
        (
            "Deploy VPC PrivateLink to allow private connections to services.",
            "Separate information flows logically or physically.",
        ),
        (
            "Use Cloud Armor rules to protect against common web attacks.",
            "Implement controls for application self-protection at runtime.",
        ),
        (
            "Use AWS Managed Rules for WAF to deploy built-in baseline groups.",
            "Implement common secure configuration settings.",
        ),
        (
            "Associate the WAF policy with the service endpoint.",
            "Monitor communications at external managed interfaces.",
        ),
        (
            "Tune custom WAF rules and exclusions to reduce false positives.",
            "Ensure the accuracy of monitoring results.",
        ),
        (
            "Use custom routes to force traffic through a network appliance for security control.",
            "Control communications through key internal managed interfaces.",
        ),
        (
            "Use a topology enabling centralized traffic inspection across network boundaries.",
            "Provide visibility into network traffic at external and key internal "
            "system interfaces.",
        ),
        (
            "Use Azure Security Center to detect misconfiguration risks.",
            "Vulnerabilities in assets are identified, validated, and recorded.",
        ),
        (
            "Detect threats in cloud environments.",
            "Assets are monitored to find anomalies and potentially adverse events.",
        ),
        (
            "Service-native firewalls limit exposure and disable public access.",
            "Monitor and control communications at external managed interfaces.",
        ),
        (
            "Use a regulatory compliance dashboard to monitor compliance.",
            "Continuous risk monitoring includes compliance monitoring.",
        ),
        (
            "Deny creation without mandatory tags.",
            "Configuration management practices are established and applied.",
        ),
        (
            "The tags env, owner, dataClass, and costCenter are mandatory.",
            "Configuration management practices are established and applied.",
        ),
        (
            "Choose a built-in ruleset, such as OWASP Top 10 vulnerabilities, "
            "and tune it to the application's needs.",
            "Implement controls for application self-protection at runtime.",
        ),
        (
            "Add custom rules and exclusions to reduce false positives based on "
            "the threat landscape and application security profile.",
            "Ensure the accuracy, currency, and availability of monitoring results.",
        ),
    ],
)
def test_platform_alignment_recognizes_additional_explicit_mechanisms(
    child: str,
    parent: str,
):
    assert standardized_mechanism_supports(child, parent)


def test_standardized_mechanisms_do_not_invent_behavior_from_product_topics():
    assert not standardized_mechanism_supports(
        "Understand and use application security groups.",
        "Enforce information flow control using policy filters.",
    )
    assert not standardized_mechanism_supports(
        "Prepare network infrastructure for Azure Firewall deployment.",
        "Monitor and control communications at external managed interfaces.",
    )
    assert not standardized_mechanism_supports(
        "Resource tags are not mandatory.",
        "Configuration management practices are established and applied.",
    )
    assert not standardized_mechanism_supports(
        "Add exclusions for application compatibility.",
        "Ensure the accuracy of monitoring results.",
    )
    assert not standardized_mechanism_supports(
        "Review VPN product options.",
        "Protect transmitted information.",
    )


def test_standardized_rescue_searches_beyond_the_retrieved_shortlist():
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Control traffic to EC2 instances with security groups.",
        excerpt="Control traffic to EC2 instances with security groups.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
    )
    distractor = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Review network traffic reports.",
        excerpt="Review network traffic reports.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    parent = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Enforce information flow control using policy filters.",
        excerpt="Enforce information flow control using policy filters.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )

    assert _standardized_parent_candidates(child, [distractor, parent], (distractor,)) == (parent,)


def test_traceability_model_receives_source_keys_and_declared_framework_references():
    reference = FrameworkReference(
        namespace="nist-sp-800-53",
        revision="5",
        control_id="sc-7.5",
        evidence_segment_id=uuid4(),
        evidence_excerpt="NIST SP 800-53 Rev.5: SC-7(5)",
    )
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Deny platform network traffic by default.",
        excerpt="Deny platform network traffic by default.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
        source_key="NS-1",
        framework_references=(reference,),
    )

    payload = traceability_input(child)

    assert payload["source_key"] == "NS-1"
    assert payload["framework_references"] == [reference.model_dump(mode="json")]


def test_declared_framework_references_augment_semantic_neighbor_budget():
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Use trusted DNS services.",
        excerpt="Use trusted DNS services.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
        framework_references=tuple(
            FrameworkReference(
                namespace="nist-sp-800-53",
                revision="5",
                control_id=control_id,
                evidence_segment_id=uuid4(),
                evidence_excerpt=control_id,
            )
            for control_id in ("sc-7", "si-4", "si-4.4", "si-4.5")
        ),
    )

    assert _parent_candidate_limit(child, 5) == 9
    assert _parent_candidate_limit(child, 20) == 24
    assert _parent_candidate_limit(child, 25, 15) == 35


def test_embedding_ranking_reserves_independent_lexical_neighbors():
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Encrypt network traffic in transit.",
        excerpt="Encrypt network traffic in transit.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
    )
    semantic = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Manage external interfaces.",
        excerpt="Manage external interfaces.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    lexical = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Protect network traffic in transit.",
        excerpt="Protect network traffic in transit.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    vectors = {
        child.statement: (1.0, 0.0),
        semantic.statement: (1.0, 0.0),
        lexical.statement: (0.0, 1.0),
    }

    ranked = _rank_parents(
        child,
        [semantic, lexical],
        vectors,
        2,
        lexical_neighbors=1,
    )

    assert ranked == (semantic, lexical)


def test_lexical_ranking_uses_weighted_terms_instead_of_whole_sentence_shape():
    child = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Restrict inbound and outbound internet traffic through a gateway.",
        excerpt="Restrict inbound and outbound internet traffic through a gateway.",
        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
    )
    short_distractor = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement="Restrict application settings through a portal.",
        excerpt="Restrict application settings through a portal.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    long_parent = TraceabilityAssertion(
        concept_id=uuid4(),
        version_id=uuid4(),
        occurrence_id=uuid4(),
        statement=(
            "Establish a traffic flow policy for each managed external interface; "
            "filter unauthorized inbound and outbound traffic from external networks; "
            "document exceptions and review them periodically."
        ),
        excerpt=(
            "Establish a traffic flow policy for each managed external interface; "
            "filter unauthorized inbound and outbound traffic from external networks; "
            "document exceptions and review them periodically."
        ),
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )

    ranked = _rank_parents(child, [short_distractor, long_parent], {}, 1)

    assert ranked == (long_parent,)


def test_platform_mapping_uses_hybrid_neighbors_by_default():
    options = ControlMappingOptions()

    assert options.semantic_neighbors == 20
    assert options.lexical_neighbors == 10


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


async def test_platform_alignment_bypasses_mapper_for_standardized_mechanisms():
    class UnexpectedMapper:
        fingerprint = "unexpected-mapper"

        async def map(self, child, parents):
            raise AssertionError("deterministic controls must not invoke the mapper")

    control = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Use network security groups to restrict traffic inbound and outbound.",
        platform="Azure",
    )
    parent = concept(
        SemanticType.TECHNICAL_REQUIREMENT,
        "Enforce information flow control using policy filters.",
    )

    check = platform_alignment_check(
        cast(UnitOfWorkFactory, ReplayStore()),
        UnexpectedMapper(),
        Embeddings(),
        concurrency=2,
    )
    result = await check.evaluate(context([control, parent]))

    assert len(result.findings) == 1
    assert result.findings[0].concept_ids == (control.id, parent.id)
    assert result.statistics["deterministic_mechanism_selections"] == 1
    assert result.model_calls == 0


async def test_platform_alignment_carries_only_findings_for_current_versions():
    class CountingMapper(Mapper):
        def __init__(self):
            self.calls = 0

        async def map(self, child, parents):
            self.calls += 1
            return await super().map(child, parents)

    control = concept(
        SemanticType.PLATFORM_REQUIREMENT,
        "Configure the service according to the adopted technical requirement.",
        platform="Azure",
    )
    parent = concept(
        SemanticType.TECHNICAL_REQUIREMENT,
        "Manage the service configuration according to organizational requirements.",
    )
    mapper = CountingMapper()
    check = platform_alignment_check(
        cast(UnitOfWorkFactory, ReplayStore()),
        mapper,
        Embeddings(),
        concurrency=2,
    )
    first = await check.evaluate(context([control, parent]))
    carried = await check.evaluate(context([control, parent], snapshot_findings=first.findings))

    changed_version_id = uuid4()
    changed_parent = parent.model_copy(
        update={
            "versions": [parent.versions[0].model_copy(update={"id": changed_version_id})],
            "occurrences": [
                parent.occurrences[0].model_copy(update={"concept_version_id": changed_version_id})
            ],
        }
    )
    refreshed = await check.evaluate(
        context([control, changed_parent], snapshot_findings=first.findings)
    )

    assert mapper.calls == 2
    assert carried.findings == first.findings
    assert carried.statistics["platform_parent_selections_carried"] == 1
    assert refreshed.statistics["platform_parent_selections_carried"] == 0
    assert changed_version_id in refreshed.findings[0].version_ids


class EvaluationMapper(Mapper):
    fingerprint = "evaluation-mapper-test"

    async def map(self, child, parents):
        expected_positive = (
            "detection, investigation, and remediation",
            "DDoS standard protection",
            "backups at the different service tiers",
            "endpoint detection and response",
            "Adaptive Network Hardening",
        )
        if not any(value in child.statement for value in expected_positive):
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


class CapturingMapper:
    fingerprint = "capturing-mapper-test"

    def __init__(self):
        self.calls = []

    async def map(self, child, parents):
        self.calls.append((child, parents))
        return TraceabilityVerification(
            selection=TraceabilitySelection(unresolved_reason="Captured evaluator input."),
            method="test",
        )


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


async def test_provenance_evaluator_forwards_source_keys_and_framework_references(tmp_path):
    source = json.loads((Path("evals") / "provenance-v1.json").read_text(encoding="utf-8"))
    reference = {
        "namespace": "nist-sp-800-53",
        "revision": "5",
        "control_id": "ir-4",
        "evidence_segment_id": str(uuid4()),
        "evidence_excerpt": "NIST SP 800-53 Rev.5: IR-4",
    }
    source["assertions"]["azure-incident"]["source_key"] = "IM-1"
    source["assertions"]["azure-incident"]["framework_references"] = [reference]
    source["assertions"]["nist-incident"]["source_key"] = "ir-4"
    dataset = tmp_path / "provenance-input-fields.json"
    dataset.write_text(json.dumps(source), encoding="utf-8")
    mapper = CapturingMapper()

    await evaluate_provenance(
        dataset,
        tmp_path / "report.json",
        mapper,
        split="all",
        history_dir=tmp_path / "history",
    )

    child, parents = next(
        call for call in mapper.calls if call[0].statement.startswith("Enable detection")
    )
    assert child.source_key == "IM-1"
    assert child.framework_references[0].control_id == "ir-4"
    assert any(parent.source_key == "ir-4" for parent in parents)


def test_complete_provenance_dataset_covers_every_reviewed_control():
    _, dataset = load_provenance_dataset(Path("evals") / "provenance-complete-v1.json")
    nat_gateway = next(
        case for case in dataset.cases if case.child_key == "c5172e66-dc4c-5cb4-aa2f-251f5e183002"
    )

    assert dataset.review_status == "independently_reviewed"
    assert len(dataset.cases) == 385
    assert sum(not case.expected_parent_keys for case in dataset.cases) == 117
    assert set(nat_gateway.expected_parent_keys) == {
        "3051e9bf-453c-57ae-89a0-f9f5b5be4b6c",
        "63c2e21d-2d43-59d7-84f1-272f5c694e32",
    }


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


async def test_provenance_evaluation_reports_expected_parent_missing_from_candidates(tmp_path):
    source = json.loads((Path("evals") / "provenance-v1.json").read_text(encoding="utf-8"))
    case = next(item for item in source["cases"] if item["id"] == "no-defensible-parent")
    case["expected_parent_keys"] = ["nist-default-deny"]
    dataset = tmp_path / "retrieval-miss.json"
    dataset.write_text(json.dumps(source), encoding="utf-8")

    report = await evaluate_provenance(
        dataset,
        tmp_path / "report.json",
        EvaluationMapper(),
        split="test",
        history_dir=tmp_path / "history",
    )

    result = next(item for item in report["case_results"] if item["id"] == case["id"])
    assert report["passed"] is False
    assert report["retrieval_recall"] < 1
    assert result["missing_candidates"] == ["nist-default-deny"]


def test_ordered_check_cache_depends_on_prior_output_revisions():
    async def evaluate(_context: CheckContext) -> CheckResult:
        return CheckResult()

    check = Check("downstream", evaluate)
    options = CheckOptions()

    first = _check_cache_key(check, options, "input", ["upstream-a"])
    second = _check_cache_key(check, options, "input", ["upstream-b"])

    assert first != second
