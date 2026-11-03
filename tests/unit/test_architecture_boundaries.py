"""Executable architecture fitness checks; see AGENTS.md "Architecture conformance"."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "ekb"
INFRASTRUCTURE = ("sqlalchemy", "asyncpg", "fastapi", "httpx", "azure", "openai", "textual")
INFRASTRUCTURE += ("agent_framework", "pydantic_evals")

# Existing interface exceptions. Do not grow this list: route new needs through services/runtime.
INTERFACE_EXCEPTIONS = {
    ("api/__init__.py", "sqlalchemy"): "database health check",
    ("api/__init__.py", "ekb.adapters.ontology"): "read-only committed ontology catalog",
    ("cli/__init__.py", "sqlalchemy"): "doctor database check",
    ("cli/__init__.py", "sqlalchemy.exc"): "database error presentation",
    ("cli/__init__.py", "ekb.adapters.inference"): "offline rules baseline for eval",
    ("cli/workflow.py", "sqlalchemy.exc"): "database error presentation",
    ("cli/presentation.py", "sqlalchemy.engine"): "redacted database URL presentation",
    ("cli/presentation.py", "sqlalchemy.exc"): "database error presentation",
    ("cli/presentation.py", "ekb.adapters.sources"): "manifest preview without runtime",
    ("cli/evaluation.py", "ekb.adapters.inference"): "evaluation model selection",
    ("cli/architectures.py", "ekb.adapters.implementation"): "local Bicep compilation",
}


def absolute_imports(layer: str):
    root = SOURCE / layer
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            else:
                continue
            for module in modules:
                yield path.relative_to(root).as_posix(), node.lineno, module


def matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


@pytest.mark.parametrize(
    "layer", ["domain", "ports", "pipeline", "services", "orchestration", "stages"]
)
def test_inner_layers_do_not_import_infrastructure(layer):
    forbidden = ("ekb.adapters", "ekb.interfaces", "ekb.runtime", *INFRASTRUCTURE)
    if layer in {"domain", "ports"}:
        forbidden += (
            "ekb.config",
            "ekb.observability",
            "ekb.services",
            "ekb.pipeline",
            "ekb.orchestration",
            "ekb.stages",
            "ekb.evaluation",
        )
    violations = [
        f"{path}:{line} imports {module}"
        for path, line, module in absolute_imports(layer)
        if matches(module, forbidden)
    ]
    assert not violations


def test_adapters_do_not_depend_on_use_cases_or_interfaces():
    forbidden = ("ekb.interfaces", "ekb.services", "ekb.pipeline", "ekb.runtime")
    forbidden += ("ekb.orchestration", "ekb.stages")
    violations = [
        f"{path}:{line} imports {module}"
        for path, line, module in absolute_imports("adapters")
        if matches(module, forbidden)
    ]
    assert not violations


def test_interfaces_stay_thin():
    forbidden = ("ekb.adapters", *INFRASTRUCTURE)
    allowed = ("fastapi", "httpx", "textual")  # presentation frameworks belong here
    found = {
        (path, module)
        for path, _, module in absolute_imports("interfaces")
        if matches(module, forbidden) and not matches(module, allowed)
    }
    assert found <= INTERFACE_EXCEPTIONS.keys(), sorted(found - INTERFACE_EXCEPTIONS.keys())
    assert not INTERFACE_EXCEPTIONS.keys() - found, "remove resolved exceptions"
