import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "main.bicep"


@pytest.fixture(scope="module")
def compiled_infra() -> dict[str, Any]:
    az = shutil.which("az")
    if az is None:
        pytest.skip("Offline infrastructure validation requires Azure CLI (az not found)")
    environment = {
        **os.environ,
        "AZURE_BICEP_CHECK_VERSION": "false",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
    }

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [az, "bicep", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )

    version = run("version")
    if version.returncode != 0:
        pytest.skip(
            "Offline infrastructure validation requires an installed Bicep compiler: "
            + (version.stderr or version.stdout).strip()
        )
    build = run("build", "--file", str(TEMPLATE), "--stdout", "--no-restore")
    assert build.returncode == 0, build.stderr or build.stdout
    lint = run("lint", "--file", str(TEMPLATE), "--no-restore")
    assert lint.returncode == 0, lint.stderr or lint.stdout
    assert not lint.stderr.strip(), lint.stderr
    return json.loads(build.stdout)


def test_infra_is_model_only_and_entra_authenticated(compiled_infra: dict[str, Any]):
    resources = compiled_infra["resources"]
    assert {resource["type"] for resource in resources.values()} == {
        "Microsoft.CognitiveServices/accounts",
        "Microsoft.CognitiveServices/accounts/projects",
        "Microsoft.CognitiveServices/accounts/deployments",
        "Microsoft.Authorization/roleAssignments",
    }
    accounts = [
        resource
        for resource in resources.values()
        if resource["type"] == "Microsoft.CognitiveServices/accounts"
    ]
    assert len(accounts) == 1
    assert accounts[0]["kind"] == "AIServices"
    assert accounts[0]["properties"]["disableLocalAuth"] is True
    role = resources["operatorInferenceRole"]
    assert role["condition"] == "[not(empty(parameters('operatorPrincipalId')))]"
    assert role["scope"] == compiled_infra["outputs"]["accountId"]["value"]
    assert compiled_infra["parameters"]["operatorPrincipalId"]["defaultValue"] == ""


def test_infra_requires_explicit_student_model(compiled_infra: dict[str, Any]):
    student = compiled_infra["parameters"]["student"]
    assert "defaultValue" not in student
    assert not student.get("nullable", False)
    definition = compiled_infra["definitions"][student["$ref"].split("/")[-1]]
    for field in ("modelName", "modelVersion"):
        property_schema = definition["properties"][field]
        assert not property_schema.get("nullable", False)
        assert property_schema["minLength"] >= 1
    deployment = compiled_infra["resources"]["studentDeployment"]
    assert deployment["properties"]["model"]["name"] == "[parameters('student').modelName]"
    assert deployment["properties"]["model"]["version"] == "[parameters('student').modelVersion]"


def test_infra_only_enables_teacher_when_explicitly_configured(compiled_infra: dict[str, Any]):
    resources = compiled_infra["resources"]
    deployments = {
        name: resource
        for name, resource in resources.items()
        if resource["type"] == "Microsoft.CognitiveServices/accounts/deployments"
    }
    assert set(deployments) == {"studentDeployment", "embeddingDeployment", "teacherDeployment"}
    assert "condition" not in deployments["studentDeployment"]
    assert "condition" not in deployments["embeddingDeployment"]
    teacher = compiled_infra["parameters"]["teacher"]
    assert teacher["nullable"] is True
    assert teacher.get("defaultValue") is None
    assert deployments["teacherDeployment"]["condition"] == (
        "[not(equals(parameters('teacher'), null()))]"
    )
    assert deployments["teacherDeployment"]["properties"]["model"]["name"] == (
        "[parameters('teacher').modelName]"
    )
    assert resources["project"]["condition"] == "[parameters('createProject')]"
