import re

from ekb.domain.models import (
    Assertion,
    AssertionDraft,
    Candidate,
    ConceptVersion,
    Modality,
    Provenance,
    fingerprint,
)


def assertion_fingerprint(version: ConceptVersion, evidence: Provenance, writer: str) -> str:
    return fingerprint(
        writer=writer,
        version=version.id,
        candidate=version.candidate.model_dump(mode="json"),
        occurrence=evidence.occurrence.id,
        segment=evidence.segment.model_dump(mode="json"),
        source=evidence.source.item.name,
    )


def validate_assertion(candidate: Candidate, draft: AssertionDraft) -> None:
    if draft.text is None:
        return
    text = draft.text
    if text.startswith(("#", "-", "*", "\u2022")) or not text.endswith("."):
        raise ValueError("An assertion must be a sentence, not a heading or bullet")
    if re.match(r"^(it|this|that|they|we|our|these|those|must|shall|should)\b", text, re.I):
        raise ValueError("An assertion needs an explicit supported subject")
    if "{{" in text or "}}" in text:
        raise ValueError("An assertion cannot contain an unresolved template parameter")
    for qualifier in ("not", "no", "never", "without", "only", "unless", "except"):
        if re.search(rf"\b{qualifier}\b", candidate.statement, re.I) and not re.search(
            rf"\b{qualifier}\b", text, re.I
        ):
            raise ValueError(f"Assertion dropped qualifier: {qualifier}")
    numbers = set(re.findall(r"\b\d+(?:[.,]\d+)*%?", candidate.statement))
    if not numbers <= set(re.findall(r"\b\d+(?:[.,]\d+)*%?", text)):
        raise ValueError("Assertion dropped a numeric qualifier")
    if candidate.modality == Modality.MANDATED and not re.search(r"\b(must|shall)\b", text, re.I):
        raise ValueError("Assertion weakened an explicit obligation")
    if candidate.modality == Modality.RECOMMENDED and (
        not re.search(r"\bshould\b", text, re.I) or re.search(r"\b(must|shall)\b", text, re.I)
    ):
        raise ValueError("Assertion changed a recommendation into an obligation")
    if (
        candidate.modality == Modality.UNSPECIFIED
        and not re.search(r"\b(must|shall)\b", candidate.statement, re.I)
        and re.search(r"\b(must|shall)\b", text, re.I)
    ):
        raise ValueError("Assertion invented an obligation")


def existing_assertion(candidate: Candidate) -> str | None:
    text = candidate.statement
    if (
        len(text) > 250
        or "\n" in text
        or not text[0].isupper()
        or "{{" in text
        or re.search(r"[.!?]\s+[A-Z]", text)
    ):
        return None
    if not re.search(r"\b(must|shall|should|aims to|seeks to)\b", text):
        return None
    try:
        validate_assertion(candidate, AssertionDraft(text=text))
    except ValueError:
        return None
    return text


def checked_assertion(candidate: Candidate, assertion: Assertion) -> Assertion:
    try:
        validate_assertion(candidate, assertion)
    except ValueError:
        return assertion.model_copy(
            update={
                "text": None,
                "uncertainty": "Cached phrasing needs clarification; inspect the original quote.",
            }
        )
    return assertion
