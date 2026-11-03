import json
from datetime import date

import pytest
from pydantic import SecretStr
from test_assessment_service import seed_requirement

from ekb.config import Settings
from ekb.domain.baselines import BaselineApproval
from ekb.domain.resolution import Decision
from ekb.runtime import Runtime

pytestmark = pytest.mark.integration


@pytest.fixture
async def runtime(database, tmp_path):
    runtime = Runtime(
        Settings(
            data_dir=tmp_path / "runtime",
            database_url=SecretStr("postgresql+asyncpg://ekb:ekb@localhost/ekb"),
        )
    )
    await runtime.database.close()
    runtime.database = database
    yield runtime
    await runtime.__aexit__()


def policy(*versions, version="1"):
    return json.dumps(
        {
            "id": "network-baseline",
            "version": version,
            "statement": "Every hosted workload applies the network baseline before release.",
            "requirements": [
                {"concept_id": str(concept), "version_id": str(version_id), "options": []}
                for concept, version_id in versions
            ],
        },
        indent=2,
    ).encode()


async def test_registration_retains_policy_and_only_proposes_adoption(runtime, database):
    adopted = await seed_requirement(database, "Protect networks from flooding.")
    pending = await seed_requirement(database, "Enable DDoS protection.", approve=False)
    pins = [(item.id, item.versions[0].id) for item in (adopted, pending)]
    content = policy(*pins)

    first = await runtime.baselines.register(content, "file:///baseline.json")
    # The baseline and the unadopted requirement await a human decision; nothing is approved.
    assert len(first.pending_approvals) == 2
    assert await runtime.baselines.register(content, "file:///baseline.json") == first
    approval = BaselineApproval(concept_id=first.concept_id, version_id=first.version_id)
    loaded, digest = await runtime.assessments.policy(approval, date.today())
    assert digest == first.policy_sha256 and len(loaded.requirements) == 2

    for finding_id in first.pending_approvals:
        await runtime.reconciliation.decide(
            Decision(
                finding_id=finding_id,
                outcome="accepted",
                actor="architecture-board",
                rationale="Adopt the reviewed baseline and its requirement.",
            )
        )
    again = await runtime.baselines.register(content, "file:///baseline.json")
    assert again.pending_approvals == ()

    revised = await runtime.baselines.register(policy(*pins, version="2"), "file:///b.json")
    assert revised.concept_id == first.concept_id and revised.version_id != first.version_id
    assert len(revised.pending_approvals) == 1
    pinned = BaselineApproval(concept_id=revised.concept_id, version_id=revised.version_id)
    assert (await runtime.assessments.policy(pinned, date.today()))[0].version == "2"

    with pytest.raises(ValueError, match="does not exist"):
        await runtime.baselines.register(policy((pins[0][0], pins[1][1])), "file:///x.json")
