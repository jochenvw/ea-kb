"""End-to-end reference application: obligation -> baseline -> design -> Bicep/ARM -> findings."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr
from test_assessment_service import seed_requirement

from ekb.config import Settings
from ekb.domain.architectures import ArchitectureControl, ArchitectureManifest
from ekb.domain.assessment import SolutionAssessment, SolutionAssessmentInput
from ekb.domain.baselines import BaselineApproval
from ekb.domain.models import Scope, SemanticType
from ekb.evaluation.architectures import DesignSpec, RequirementSpec, build_design, build_policy
from ekb.interfaces.api import create_app
from ekb.interfaces.mcp import create_server
from ekb.runtime import Runtime
from ekb.services.architectures import ArchitectureService

pytestmark = pytest.mark.integration

FIXTURE = Path("tests") / "fixtures" / "reference-app"
EXAMPLE = Path("examples") / "reference-app"
TEMPLATE = EXAMPLE / "main.json"


def load(name: str) -> Any:
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def design_for(case: dict[str, Any]) -> DesignSpec:
    raw = load(f"design/{case['design']}.json")
    decisions = {item["requirement"]: item for item in raw["decisions"]}
    for key, override in case.get("decisions", {}).items():
        if override is None:
            decisions.pop(key, None)
        else:
            decisions[key] = {"requirement": key, **override}
    raw["decisions"] = list(decisions.values())
    raw["context"] = {**raw["context"], **case.get("context", {})}
    return DesignSpec.model_validate(raw)


@pytest.fixture
async def reference(database, tmp_path):
    runtime = Runtime(
        Settings(
            data_dir=tmp_path / "runtime",
            database_url=SecretStr("postgresql+asyncpg://ekb:ekb@localhost/ekb"),
        )
    )
    await runtime.database.close()
    runtime.database = database
    runtime.architectures = ArchitectureService(database, runtime.artifacts)
    catalog = load("requirements.json")
    specs = [RequirementSpec.model_validate(item) for item in catalog["requirements"]]
    # Authority must already be effective at the fixture's fixed assessment date.
    effective = date(2026, 1, 1)
    concepts = {
        spec.key: await seed_requirement(database, spec.statement, effective_at=effective)
        for spec in specs
    }
    versions = {key: (item.id, item.versions[0].id) for key, item in concepts.items()}
    policy = build_policy(
        catalog["policy"]["id"],
        catalog["policy"]["version"],
        catalog["obligation"],
        specs,
        versions,
    )
    raw = policy.model_dump_json()
    root = await seed_requirement(
        database, catalog["obligation"], raw, SemanticType.STRATEGIC_REQUIREMENT, effective
    )
    await runtime.artifacts.put(raw.encode())
    approval = BaselineApproval(concept_id=root.id, version_id=root.versions[0].id)
    yield runtime, approval, versions, catalog
    await runtime.__aexit__()


async def assess_case(runtime: Runtime, approval, versions, case: dict[str, Any], as_of: date):
    spec = design_for(case)
    design = build_design(spec, versions)
    first = next(iter(versions.values()))
    architecture = await runtime.architectures.register(
        ArchitectureManifest(
            name=spec.solution.name or case["id"],
            description="Synthetic reference application.",
            owner=spec.solution.owner or "Synthetic owner",
            canonical_source=f"urn:example:reference-app:{case['id']}",
            version="1",
            scope=Scope(application="reference-app", environment="production"),
            controls=(ArchitectureControl(concept_id=first[0], concept_version_id=first[1]),),
        ),
        "design.json",
        "application/json",
        design.model_dump_json().encode(),
    )
    parameters = {
        key: value["value"]
        for key, value in json.loads(
            (EXAMPLE / "parameters" / f"{case['profile']}.parameters.json").read_text(
                encoding="utf-8"
            )
        )["parameters"].items()
    } | case.get("parameters", {})
    request = SolutionAssessmentInput(
        architecture_id=architecture.id,
        baseline_concept_id=approval.concept_id,
        baseline_version_id=approval.version_id,
        template=json.loads(TEMPLATE.read_text(encoding="utf-8")),
        parameters=parameters,
        commit="0123456789abcdef",
        as_of=as_of,
    )
    return request, spec, await runtime.assessments.assess(request)


async def test_reference_app_cases_match_expected_findings(reference):
    runtime, approval, versions, catalog = reference
    cases = load("cases.json")
    expected = load("expected/findings.json")
    as_of = date.fromisoformat(cases["as_of"])
    by_version = {version: key for key, (_, version) in versions.items()}
    for case in cases["cases"]:
        _, spec, result = await assess_case(runtime, approval, versions, case, as_of)
        want = expected["cases"][case["id"]]
        label = (
            case["id"],
            result.gaps,
            [(item.disposition, item.explanation) for item in result.findings],
        )
        assert result.conforms is want["conforms"], label
        assert result.acceptable_with_exceptions is want["acceptable"], label
        assert result.baseline_complete, label
        assert result.target.team == spec.solution.team, label
        assert result.target.repository == spec.solution.repository, label
        findings = {by_version[item.requirement.version_id]: item for item in result.findings}
        assert set(findings) == set(versions), label
        locations = {
            item.requirement: item.evidence_location
            for item in spec.decisions
            if item.kind != "exception"
        }
        excepted = {
            item.requirement: item.evidence_location
            for item in spec.decisions
            if item.kind == "exception"
        }
        for key, item in want["findings"].items():
            finding = findings[key]
            assert finding.disposition == item["disposition"], (label, key, finding.explanation)
            if "resource" in item:
                assert expected[item["resource"]] in finding.resources, (label, key)
            if "explains" in item:
                assert item["explains"] in finding.explanation, (label, key, finding.explanation)
            if "implementation" in item:
                assert finding.implementation == item["implementation"], (label, key)
            if key in locations and finding.applicability == "applicable":
                assert finding.decision_location == locations[key], (label, key)
            if finding.disposition == "excepted":
                assert finding.exception, (label, key)
                assert finding.exception.evidence_location == excepted[key], (label, key)
            assert finding.authoritative, (label, key)
            assert finding.provenance[0].statement == catalog["obligation"], (label, key)
            assert finding.requirement.concept_id == versions[key][0], (label, key)
        if case["id"] == "public-correct":
            # Public exposure must never produce private-network violations.
            assert not any(item.disposition == "violated" for item in result.findings)


async def test_reference_app_mcp_round_trip_uses_shared_service(reference):
    runtime, approval, versions, _ = reference
    case = load("cases.json")["cases"][1]
    request, _, direct = await assess_case(
        runtime, approval, versions, case, date.fromisoformat("2026-06-30")
    )
    server = create_server(runtime.knowledge, assessment_service=runtime.assessments)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "assess_solution", {"request": request.model_dump(mode="json")}
        )
    assert not result.isError
    remote = SolutionAssessment.model_validate(result.structuredContent)
    assert remote.conforms and remote.fingerprint == direct.fingerprint
    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/assessments", json=request.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            assert response.json()["fingerprint"] == direct.fingerprint
            # Direct, MCP and HTTP runs share inputs, so one immutable report is retained.
            response = await client.get(f"/api/architectures/{request.architecture_id}/assessments")
            assert response.status_code == 200, response.text
            retained = [item["report"]["fingerprint"] for item in response.json()]
            assert retained == [direct.fingerprint]
            missing = request.model_copy(update={"baseline_version_id": request.architecture_id})
            response = await client.post("/api/assessments", json=missing.model_dump(mode="json"))
            assert response.status_code == 422
