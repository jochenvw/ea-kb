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
        child, ("private link", "privatelink", "private endpoint", "private access point")
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
            "security groups",
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

    firewall_policy = "firewall" in child and _contains_any(
        child,
        (
            "configure azure firewall",
            "deploy and configure",
            "firewall rules",
            "firewall policy",
        ),
    )
    if firewall_policy and _contains_any(
        parent,
        (
            "information flow control",
            "external managed interfaces",
            "controlling the flow of information",
        ),
    ):
        return True

    central_management = _contains_any(
        child,
        (
            "central management",
            "centrally manage",
            "firewall manager",
            "firewall policy",
        ),
    )
    if central_management and "centrally manage" in parent:
        return True

    waf = _contains_any(child, ("web application firewall", "waf"))
    if (
        waf
        and _contains_any(
            child,
            (
                "block malicious",
                "detection mode",
                "prevention mode",
                "protect applications",
                "protect apis",
            ),
        )
        and _contains_any(
            parent,
            (
                "information flow control",
                "external managed interfaces",
                "validity of the following information inputs",
            ),
        )
    ):
        return True

    application_firewall = _contains_any(child, ("web application firewall", "waf", "cloud armor"))
    if (
        application_firewall
        and _contains_any(
            child,
            (
                "application-specific attacks",
                "bot protection",
                "common web attacks",
                "deploy a web application firewall",
                "modsecurity",
                "owasp",
                "protect applications",
                "protect web applications",
                "web-application vulnerabilities",
            ),
        )
        and "application self-protection at runtime" in parent
    ):
        return True
    if (
        "built-in ruleset" in child
        and "owasp" in child
        and "application self-protection at runtime" in parent
    ):
        return True
    if (
        application_firewall
        and _contains_any(child, ("baseline groups", "built-in ruleset", "managed rules"))
        and "configuration settings" in parent
    ):
        return True
    if (
        waf
        and "associate" in child
        and _contains_any(child, ("service endpoint", "application gateway", "front door"))
        and "external managed interfaces" in parent
    ):
        return True
    false_positive_tuning = "false positives" in child and _contains_any(
        child, ("custom rules", "exclusions", "tune", "reduce")
    )
    if false_positive_tuning and _contains_any(parent, ("accuracy", "monitoring results")):
        return True

    private_connectivity = "private connectivity" in child
    if private_connectivity and _contains_any(
        parent,
        (
            "separate information flows",
            "network integrity is protected",
            "unauthorized logical access",
        ),
    ):
        return True

    if "network security perimeter" in child and _contains_any(
        parent,
        (
            "separate information flows",
            "network integrity is protected",
            "unauthorized logical access",
        ),
    ):
        return True

    vpn = _contains_any(child, ("virtual private network", " vpn ", "ipsec"))
    if (
        vpn
        and _contains_any(child, ("secure connection", "site-to-site", "point-to-site"))
        and ("transmitted information" in parent or "remote access is managed" in parent)
    ):
        return True

    if (
        _contains_any(child, ("avoid assigning public ips", "avoid public ip", "without public ip"))
        and _contains_any(child, ("application gateway", "load balancer", "front-end"))
        and _contains_any(parent, ("direct connection", "managed interface"))
    ):
        return True

    if (
        "vpc peering" in child
        and _contains_any(child, ("create", "accept", "connection"))
        and "authorize internal connections" in parent
    ):
        return True

    if (
        "custom routes" in child
        and _contains_any(child, ("forced through", "network appliance", "security control"))
        and _contains_any(
            parent,
            ("external managed interfaces", "key internal managed interfaces"),
        )
    ):
        return True

    if (
        "centralized traffic inspection" in child
        and _contains_any(child, ("network boundaries", "network topology"))
        and _contains_any(
            parent,
            ("visibility into network traffic", "external and key internal system interfaces"),
        )
    ):
        return True

    if (
        "azure policy" in child
        and _contains_any(child, ("require", "mandatory", "enforce"))
        and _contains_any(parent, ("configuration settings", "configuration management"))
    ):
        return True

    if _contains_any(child, ("transition mode", "enforced mode")) and _contains_any(
        parent, ("configuration settings", "restrictive mode")
    ):
        return True

    if (
        _contains_any(child, ("allow list", "allow lists", "allowlist"))
        and traffic_mechanism
        and _contains_any(parent, ("approved authorizations", "information flow control"))
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
    ) and _contains_any(
        parent,
        (
            "analyze and correlate audit records",
            "centrally review and analyze audit records",
            "system monitoring",
        ),
    ):
        return True
    if (
        "sentinel" in child
        and _contains_any(child, ("centralize security analysis", "security analysis"))
        and _contains_any(parent, ("review and analyze audit records", "security analysis"))
    ):
        return True

    dangling_dns = _contains_any(
        child, ("dangling dns", "subdomain takeover", "custom domain")
    ) and _contains_any(child, ("detect", "identify", "decommission"))
    if dangling_dns and _contains_any(
        parent,
        (
            "monitor and scan for vulnerabilities",
            "monitored to find anomalies",
            "monitored to find potentially adverse events",
        ),
    ):
        return True

    if "dnssec" in child and _contains_any(
        parent,
        (
            "data origin authentication",
            "integrity verification artifacts",
            "chain of trust",
        ),
    ):
        return True

    if _contains_any(child, ("sender policy framework", "spf")) and _contains_any(
        parent, ("spam protection", "unsolicited messages")
    ):
        return True

    if (
        "nist sp 800-81" in child
        and _contains_any(child, ("follow", "deployment guide", "best practices"))
        and "security and privacy engineering principles" in parent
    ):
        return True

    if (
        _contains_any(child, ("high-quality alerts", "threat detection"))
        and _contains_any(child, ("detection", "investigation", "remediation", "alerts"))
        and _contains_any(
            parent,
            (
                "incident handling capability",
                "security alerts",
                "adverse events are analyzed",
            ),
        )
    ):
        return True

    mandatory_tags = "tags" in child and "mandatory" in child and "not mandatory" not in child
    if (
        mandatory_tags
        or _contains_any(
            child,
            (
                "apply caf naming",
                "naming conventions",
            ),
        )
    ) and "configuration management practices" in parent:
        return True

    if (
        _contains_any(child, ("detect misconfiguration", "detect the misconfiguration"))
        and _contains_any(child, ("security center", "defender for cloud"))
        and _contains_any(
            parent,
            (
                "vulnerabilities in assets are identified",
                "monitor and scan for vulnerabilities",
                "identify, report, and correct system flaws",
            ),
        )
    ):
        return True

    if _contains_any(child, ("detect threats", "threat detection")) and _contains_any(
        parent,
        (
            "assets are monitored to find",
            "networks and network services are monitored to find",
        ),
    ):
        return True

    if (
        "service-native firewalls" in child
        and _contains_any(child, ("limit exposure", "disabling public access"))
        and _contains_any(
            parent,
            ("external managed interfaces", "key internal managed interfaces"),
        )
    ):
        return True

    if (
        "regulatory compliance dashboard" in child
        and _contains_any(child, ("monitor", "compliance"))
        and "compliance monitoring" in parent
    ):
        return True

    if (
        "privileged access" in child
        and _contains_any(child, ("administrative accounts", "privileged access workstations"))
        and _contains_any(
            parent,
            (
                "privileged accounts",
                "least privilege",
                "logical access",
                "access enforcement",
            ),
        )
    ):
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
