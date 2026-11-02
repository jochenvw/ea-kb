from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.models import Model, Scope, SemanticType, Usage


def _contains_any(statement: str, terms: tuple[str, ...]) -> bool:
    return any(term in statement for term in terms)


def standardized_mechanism_supports(child_statement: str, parent_statement: str) -> bool:
    """Return whether an explicit standardized mechanism directly implements a parent clause."""
    child = child_statement.casefold()
    parent = parent_statement.casefold()

    private_access = _contains_any(
        child, ("private link", "private endpoint", "private access point")
    )
    if private_access and (
        "separate information flows" in parent
        or "network integrity is protected" in parent
        or "public network" in parent
    ):
        return True

    private_dns = _contains_any(child, ("private dns", "private hosted zone")) and _contains_any(
        child, ("dns resolution", "dns queries", "name resolution")
    )
    if private_dns and _contains_any(
        parent,
        (
            "name/address resolution",
            "internal and external role separation",
            "boundary protection",
            "public network",
        ),
    ):
        return True

    segmentation = _contains_any(child, ("segment", "segmentation")) and _contains_any(
        child, ("network", "subnet", "traffic", "resources")
    )
    if segmentation and _contains_any(
        parent,
        (
            "network segregation",
            "network segmentation",
            "boundary protection",
            "separate subnetworks",
            "separate information flows",
        ),
    ):
        return True

    traffic_action = _contains_any(
        child,
        (
            "allow or deny",
            "allow and deny",
            "block access",
            "filter traffic",
            "filter outbound",
            "restrict traffic",
            "control traffic",
            "traffic filtering",
        ),
    )
    traffic_mechanism = _contains_any(
        child,
        (
            "firewall",
            "network acl",
            "network security group",
            "security group rules",
        ),
    )
    if (
        traffic_action
        and traffic_mechanism
        and _contains_any(
            parent,
            (
                "information flow control",
                "controlling the flow of information",
                "policy filters",
            ),
        )
    ):
        return True

    if "single sign-on" in child and "single sign-on" in parent:
        return True
    if _contains_any(child, ("managed identities", "service principals")) and _contains_any(
        parent, ("identities and credentials", "identity providers")
    ):
        return True

    if _contains_any(
        child, ("static application security testing", "static code analysis", "sast")
    ) and _contains_any(parent, ("static code analysis", "secure software development")):
        return True
    if _contains_any(
        child, ("vulnerability management", "vulnerability scan", "scan for vulnerabilities")
    ) and _contains_any(
        parent, ("vulnerability monitoring", "monitor and scan for vulnerabilities")
    ):
        return True

    denial_of_service = _contains_any(
        child,
        ("ddos", "denial-of-service", "denial of service", "amplification attack", "flood attack"),
    )
    if (
        denial_of_service
        and _contains_any(parent, ("denial-of-service", "denial of service"))
        and not _contains_any(parent, ("manage capacity", "bandwidth", "redundancy"))
    ):
        return True

    if _contains_any(child, ("encrypt", "encryption", " tls ", "https")) and _contains_any(
        parent, ("cryptographic", "encrypt", "confidentiality")
    ):
        return True
    if _contains_any(
        child,
        ("antimalware", "anti-malware", "antivirus", "endpoint detection and response", "edr"),
    ) and _contains_any(parent, ("malicious code", "host-based monitoring")):
        return True
    if (
        "host" in child
        and _contains_any(child, ("ids", "ips", "intrusion detection", "edr"))
        and _contains_any(parent, ("host-based monitoring", "monitor the system to detect"))
    ):
        return True

    if _contains_any(child, ("backup", "snapshot", "recovery vault")) and _contains_any(
        parent, ("backup", "recover", "recovery")
    ):
        return True
    if _contains_any(child, ("hardening", "secure baseline", "deny by default")) and _contains_any(
        parent, ("configuration settings", "restrictive mode", "secure configurations")
    ):
        return True
    if _contains_any(
        child, ("flow logs", "diagnostic logs", "collect logs", "centralize logs", "audit logs")
    ) and _contains_any(parent, ("audit records", "audit log", "event logging")):
        return True
    if _contains_any(
        child, ("siem", "security information and event management")
    ) and _contains_any(parent, ("analyze and correlate audit records", "system monitoring")):
        return True
    return False


class FrameworkReference(Model):
    namespace: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    control_id: str = Field(min_length=1)
    evidence_segment_id: UUID
    evidence_excerpt: str = Field(min_length=1)


class TraceabilityAssertion(Model):
    concept_id: UUID
    version_id: UUID
    occurrence_id: UUID
    statement: str = Field(min_length=1, max_length=4000)
    excerpt: str = Field(min_length=1)
    semantic_type: SemanticType
    scope: Scope = Field(default_factory=Scope)
    source_key: str | None = None
    framework_references: tuple[FrameworkReference, ...] = ()


class ParentSelection(Model):
    parent_concept_id: UUID
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    child_excerpt: str = Field(min_length=1)
    parent_excerpt: str = Field(min_length=1)


class TraceabilitySelection(Model):
    links: tuple[ParentSelection, ...] = Field(default=(), max_length=3)
    unresolved_reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def unique_parent_selections(self) -> Self:
        parent_ids = [link.parent_concept_id for link in self.links]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("Traceability selections require unique parent concepts")
        if not self.links and not self.unresolved_reason:
            raise ValueError("An empty selection requires an unresolved reason")
        return self


class TraceabilityVerification(Model):
    selection: TraceabilitySelection
    status: Literal["completed", "failed", "context_limit"] = "completed"
    method: str
    model_id: str | None = None
    model_calls: int = Field(default=0, ge=0)
    usage: Usage = Field(default_factory=Usage)

    @property
    def completed(self) -> bool:
        return self.status == "completed"
