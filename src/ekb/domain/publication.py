from __future__ import annotations

import re
from datetime import date
from uuid import UUID

from ekb.domain.models import (
    AuthorityEvent,
    AuthorityState,
    CanonicalStatus,
    GovernanceStatus,
    PublicationEvidence,
    PublicationMetadata,
    PublicationStatus,
    Scope,
    Segment,
    SemanticVersion,
    SourceItem,
    fingerprint,
)

PUBLICATION_DETECTOR_VERSION = "publication-v1"

_STATUSES = (
    (r"\bwithdrawn\b", PublicationStatus.WITHDRAWN),
    (r"\bsuperseded\b", PublicationStatus.SUPERSEDED),
    (r"\bdeprecated\b", PublicationStatus.DEPRECATED),
    (r"\bworking[ _-]draft\b", PublicationStatus.WORKING_DRAFT),
    (r"\brelease[ _-]candidate\b|\brc(?:[.-][0-9]+)?\b", PublicationStatus.RELEASE_CANDIDATE),
    (r"\bconsultation\b", PublicationStatus.CONSULTATION),
    (r"\bpreview\b", PublicationStatus.PREVIEW),
    (r"\balpha\b", PublicationStatus.ALPHA),
    (r"\bbeta\b", PublicationStatus.BETA),
    (r"\bdraft\b", PublicationStatus.DRAFT),
    (r"\bproposed\b", PublicationStatus.PROPOSED),
    (r"\bfinal\b", PublicationStatus.FINAL),
    (r"\bpublished\b", PublicationStatus.PUBLISHED),
    (r"\bapproved\b", PublicationStatus.APPROVED),
)
_SEMVER = re.compile(
    r"(?<![0-9A-Za-z.])v?([0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(?![0-9A-Za-z.+-])"
)


def publication_fingerprint(item: SourceItem, parser_fingerprint: str) -> str:
    return fingerprint(
        detector=PUBLICATION_DETECTOR_VERSION,
        parser=parser_fingerprint,
        name=item.name,
        metadata={
            key: item.metadata.get(key)
            for key in (
                "publication_status",
                "status",
                "title",
                "publisher_version",
                "version",
                "approval_status",
            )
        },
    )


def assess_publication(item: SourceItem, segments: list[Segment]) -> PublicationMetadata:
    signals: list[tuple[str, PublicationEvidence, bool]] = []
    for field in (
        "publication_status",
        "status",
        "title",
        "publisher_version",
        "version",
        "approval_status",
    ):
        value = item.metadata.get(field)
        if isinstance(value, str) and value.strip() and value != "unknown":
            signals.append((value, PublicationEvidence(field=field, excerpt=value), True))
    signals.append((item.name, PublicationEvidence(field="name", excerpt=item.name), False))
    for segment in segments:
        for line in segment.text.splitlines(keepends=True):
            stripped = line.strip()
            is_title = bool(re.match(r"^#\s+", stripped)) or (
                segment.ordinal == 0 and stripped == segment.heading
            )
            is_metadata = bool(
                re.match(
                    r"^(?:publication[_ ]status|status|version|revision|title|approval[_ ]status)"
                    r"\s*[:=]",
                    stripped,
                    re.I,
                )
            )
            self_statement = bool(
                re.match(
                    r"^(?:this|the)\s+(?:document|standard|version|benchmark|guideline)"
                    r".{0,80}?\s+is\s+(?:currently\s+)?(?:a\s+|in\s+)?"
                    r"(?:draft|preview|final|withdrawn|superseded|deprecated|published)\b",
                    stripped,
                    re.I,
                )
            )
            if not (is_title or is_metadata or self_statement):
                continue
            start = segment.text.index(line) + len(line) - len(line.lstrip())
            line_reference = PublicationEvidence(
                excerpt=stripped,
                segment_id=segment.id,
                start=start,
                end=start + len(stripped),
            )
            signals.append((stripped, line_reference, is_metadata or is_title))

    statuses: list[tuple[PublicationStatus, PublicationEvidence]] = []
    publisher_version = None
    semantic_version = None
    evidence: list[PublicationEvidence] = []
    for text, reference, may_version in signals:
        normalized = text.replace("_", " ")
        for pattern, status in _STATUSES:
            if re.search(pattern, normalized, re.I):
                statuses.append((status, reference))
                break
        if publisher_version is None:
            if reference.field in {"publisher_version", "version"}:
                publisher_version = text
                evidence.append(reference)
            elif may_version and (
                match := re.match(r"^(?:version|revision)\s*[:=]\s*(.+)", text, re.I)
            ):
                publisher_version = match[1].strip()
                evidence.append(reference)
        if semantic_version is None and may_version and (match := _SEMVER.search(text)):
            try:
                semantic_version = SemanticVersion.parse(match[1])
            except ValueError:
                continue
            if publisher_version is None:
                publisher_version = match[0]
            evidence.append(reference)

    unique = {status for status, _ in statuses}
    retired = unique & {
        PublicationStatus.WITHDRAWN,
        PublicationStatus.SUPERSEDED,
        PublicationStatus.DEPRECATED,
    }
    if len(retired) == 1:
        status = next(iter(retired))
    elif len(unique) == 1:
        status = next(iter(unique))
    else:
        status = PublicationStatus.UNKNOWN
    evidence.extend(reference for _, reference in statuses)
    return PublicationMetadata(
        semantic_version=semantic_version,
        publisher_version=publisher_version,
        publication_status=status,
        evidence=tuple(dict.fromkeys(evidence)),
        notes=("Conflicting publication indicators; review the cited evidence.",)
        if len(unique) > 1 and not retired
        else (),
    )


def project_authority(
    events: list[AuthorityEvent], as_of: date, version_ids: list[UUID] | None = None
) -> list[AuthorityState]:
    states: dict[str, AuthorityState] = {}
    for version_id in version_ids or []:
        state = AuthorityState(concept_version_id=version_id, scope=Scope())
        key = fingerprint(version=version_id, scope=state.scope.model_dump())
        states[key] = state
    for event in sorted(events, key=lambda value: (value.recorded_at, str(value.id))):
        if event.effective_at > as_of or (
            event.effective_until is not None and event.effective_until < as_of
        ):
            continue
        key = fingerprint(version=event.concept_version_id, scope=event.scope.model_dump())
        state = states.get(key) or AuthorityState(
            concept_version_id=event.concept_version_id,
            scope=event.scope,
        )
        governance = {
            "review": GovernanceStatus.UNDER_REVIEW,
            "approve": GovernanceStatus.APPROVED,
            "reject": GovernanceStatus.REJECTED,
        }.get(event.action, state.governance_status)
        canonical = {
            "promote": CanonicalStatus.CANONICAL,
            "withdraw": CanonicalStatus.WITHDRAWN,
        }.get(event.action, state.canonical_status)
        states[key] = state.model_copy(
            update={
                "governance_status": governance,
                "canonical_status": canonical,
                "effective_from": event.effective_at,
                "effective_until": event.effective_until,
                "event_ids": (*state.event_ids, event.id),
            }
        )
    return list(states.values())
