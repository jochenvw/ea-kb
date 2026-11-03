"""Deterministic Azure Resource Manager (ARM) template observations.

Only a small, explicit subset of the ARM expression language is evaluated. Anything else is
unknown, so the analyzer reports what it cannot see instead of guessing from keywords.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast

from ekb.domain.assessment import CheckRequest, ImplementationObservation, VerificationStatus

ANALYZER_VERSION = "arm-template-v1"
CHECK_VERSION = "1"
MAX_COPY = 800
MAX_DEPTH = 5


class _Sentinel:
    def __init__(self, label: str):
        self.label = label

    def __repr__(self) -> str:
        return self.label


UNKNOWN = _Sentinel("unknown")
ABSENT = _Sentinel("absent")


class ExpressionError(ValueError):
    pass


_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<number>-?\d+)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<punct>[(),.\[\]]))"
)


def _tokens(text: str) -> list[tuple[str, str]]:
    position, found = 0, []
    while text[position:].strip():
        match = _TOKEN.match(text, position)
        if match is None:
            raise ExpressionError(f"Unsupported expression syntax near {text[position:][:20]!r}")
        kind = match.lastgroup or ""
        found.append((kind, match.group(kind)))
        position = match.end()
    return found


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _known(*values: object) -> bool:
    return not any(value is UNKNOWN for value in values)


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


@dataclass
class _Context:
    parameters: Mapping[str, object]
    template: Mapping[str, object]
    copy_index: int | None = None
    resolving: set[str] = field(default_factory=set)
    variables: dict[str, object] = field(default_factory=dict)

    def parameter(self, name: str) -> object:
        if name in self.parameters:
            return self.parameters[name]
        declared = _mapping(_mapping(self.template.get("parameters")).get(name))
        if "defaultValue" not in declared:
            return UNKNOWN
        return self._resolve(f"parameter:{name}", declared["defaultValue"])

    def variable(self, name: str) -> object:
        if name not in self.variables:
            declared = _mapping(self.template.get("variables"))
            if name not in declared:
                return UNKNOWN
            self.variables[name] = self._resolve(f"variable:{name}", declared[name])
        return self.variables[name]

    def _resolve(self, key: str, value: object) -> object:
        if key in self.resolving:
            return UNKNOWN
        self.resolving.add(key)
        try:
            return evaluate(value, self)
        finally:
            self.resolving.discard(key)


def _resource_id(resource_type: object, *names: object) -> object:
    if not _known(resource_type, *names) or not isinstance(resource_type, str):
        return UNKNOWN
    segments = resource_type.split("/")
    parts = [str(name) for name in names]
    if len(parts) == 1 and len(segments) > 2:
        parts = parts[0].split("/")
    if len(parts) != len(segments) - 1:
        return "/".join([resource_type, *parts])
    result = [segments[0]]
    for kind, name in zip(segments[1:], parts, strict=True):
        result.extend((kind, name))
    return "/".join(result)


def _format(template: object, *args: object) -> object:
    if not _known(template, *args) or not isinstance(template, str):
        return UNKNOWN
    return re.sub(r"\{(\d+)\}", lambda match: _text(args[int(match.group(1))]), template)


def _compare(operator: Callable[[int, int], bool]) -> Callable[[object, object], object]:
    def compare(left: object, right: object) -> object:
        if isinstance(left, int) and isinstance(right, int):
            return operator(left, right)
        return UNKNOWN

    return compare


def _and(*values: object) -> object:
    if any(value is False for value in values):
        return False
    return True if all(value is True for value in values) else UNKNOWN


def _or(*values: object) -> object:
    if any(value is True for value in values):
        return True
    return False if all(value is False for value in values) else UNKNOWN


def _if(condition: object, when_true: object, when_false: object) -> object:
    if condition is True:
        return when_true
    if condition is False:
        return when_false
    return when_true if when_true == when_false and _known(when_true) else UNKNOWN


def _concat(*values: object) -> object:
    if not _known(*values):
        return UNKNOWN
    if values and all(isinstance(value, list) for value in values):
        return [item for value in values for item in _list(value)]
    return "".join(_text(value) for value in values)


def _empty(value: object) -> object:
    if isinstance(value, str | list | dict):
        return len(cast(Sequence[object], value)) == 0
    return UNKNOWN


FUNCTIONS: dict[str, Callable[..., object]] = {
    "equals": lambda left, right: left == right if _known(left, right) else UNKNOWN,
    "not": lambda value: (not value) if isinstance(value, bool) else UNKNOWN,
    "and": _and,
    "or": _or,
    "if": _if,
    "concat": _concat,
    "format": _format,
    "resourceid": _resource_id,
    "tolower": lambda value: value.lower() if isinstance(value, str) else UNKNOWN,
    "toupper": lambda value: value.upper() if isinstance(value, str) else UNKNOWN,
    "empty": _empty,
    "greater": _compare(lambda left, right: left > right),
    "greaterorequals": _compare(lambda left, right: left >= right),
    "less": _compare(lambda left, right: left < right),
    "lessorequals": _compare(lambda left, right: left <= right),
    "true": lambda: True,
    "false": lambda: False,
    "null": lambda: None,
    "string": lambda value: _text(value) if _known(value) else UNKNOWN,
    "bool": lambda value: value if isinstance(value, bool) else UNKNOWN,
}


class _Parser:
    def __init__(self, text: str, context: _Context):
        self.tokens = _tokens(text)
        self.position = 0
        self.context = context

    def peek(self) -> str | None:
        return self.tokens[self.position][1] if self.position < len(self.tokens) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        if self.position >= len(self.tokens):
            raise ExpressionError("Unexpected end of expression")
        token = self.tokens[self.position]
        if value is not None and token[1] != value:
            raise ExpressionError(f"Expected {value} in expression")
        self.position += 1
        return token

    def parse(self) -> object:
        value = self.expression()
        if self.peek() is not None:
            raise ExpressionError("Unexpected trailing expression tokens")
        return value

    def expression(self) -> object:
        value = self.primary()
        while (token := self.peek()) in {".", "["}:
            self.take()
            if token == ".":
                key: object = self.take()[1]
            else:
                key = self.expression()
                self.take("]")
            value = _member(value, key)
        return value

    def primary(self) -> object:
        kind, text = self.take()
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "number":
            return int(text)
        if kind != "name":
            raise ExpressionError(f"Unexpected {text!r} in expression")
        if self.peek() != "(":
            return {"true": True, "false": False, "null": None}.get(text.lower(), UNKNOWN)
        self.take("(")
        arguments: list[object] = []
        while self.peek() not in {")", None}:
            arguments.append(self.expression())
            if self.peek() == ",":
                self.take(",")
        self.take(")")
        name = text.lower()
        if name in {"parameters", "variables"}:
            if len(arguments) != 1 or not isinstance(arguments[0], str):
                return UNKNOWN
            if name == "parameters":
                return self.context.parameter(arguments[0])
            return self.context.variable(arguments[0])
        if name == "copyindex":
            if self.context.copy_index is None:
                return UNKNOWN
            offset = arguments[-1] if arguments and isinstance(arguments[-1], int) else 0
            return self.context.copy_index + offset
        function = FUNCTIONS.get(name)
        # Runtime functions such as reference(), listKeys() and uniqueString() stay unknown.
        if function is None:
            return UNKNOWN
        try:
            return function(*arguments)
        except (TypeError, IndexError, ValueError):
            return UNKNOWN


def _member(value: object, key: object) -> object:
    if isinstance(value, Mapping) and isinstance(key, str):
        return _mapping(value).get(key, UNKNOWN)
    if isinstance(value, list) and isinstance(key, int):
        items = _list(value)
        return items[key] if -len(items) <= key < len(items) else UNKNOWN
    return UNKNOWN


def evaluate(value: object, context: _Context) -> object:
    if isinstance(value, str):
        if value.startswith("[["):
            return value[1:]
        if not (value.startswith("[") and value.endswith("]")):
            return value
        try:
            return _Parser(value[1:-1], context).parse()
        except ExpressionError:
            return UNKNOWN
    if isinstance(value, Mapping):
        return {key: evaluate(item, context) for key, item in _mapping(value).items()}
    if isinstance(value, list):
        return [evaluate(item, context) for item in _list(value)]
    return value


@dataclass(frozen=True)
class Resource:
    type: str
    name: object
    id: object
    location: str
    raw: Mapping[str, object]
    context: _Context
    condition: object = True
    existing: bool = False
    linked: bool = False
    problem: str | None = None

    def get(self, path: str) -> object:
        value: object = self.raw
        for key in path.split("."):
            items = _mapping(value)
            if key not in items:
                return ABSENT
            value = items[key]
        return evaluate(value, self.context)


def _resources(
    template: Mapping[str, object],
    context: _Context,
    location: str,
    condition: object = True,
    depth: int = 0,
) -> list[Resource]:
    declared = template.get("resources", [])
    if isinstance(declared, Mapping):
        # Language version 2 templates key resources by symbolic name.
        declared = list(_mapping(declared).values())
    found: list[Resource] = []
    for index, raw in enumerate(_list(declared)):
        resource = _mapping(raw)
        if not resource:
            continue
        path = f"{location}.resources[{index}]"
        copy = _mapping(resource.get("copy"))
        if not copy:
            found.extend(_instance(resource, context, path, condition, depth))
            continue
        count = evaluate(copy.get("count"), context)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_COPY:
            found.append(
                Resource(
                    type=str(resource.get("type", "")),
                    name=UNKNOWN,
                    id=UNKNOWN,
                    location=path,
                    raw=resource,
                    context=context,
                    condition=condition,
                    problem="The copy count cannot be resolved from the supplied artefacts.",
                )
            )
            continue
        for number in range(count):
            local = _Context(context.parameters, context.template, number, set(), context.variables)
            found.extend(_instance(resource, local, f"{path}[{number}]", condition, depth))
    return found


def _instance(
    resource: Mapping[str, object],
    context: _Context,
    location: str,
    inherited: object,
    depth: int,
) -> list[Resource]:
    kind = str(resource.get("type", ""))
    name = evaluate(resource.get("name"), context)
    condition = _and(inherited, evaluate(resource.get("condition", True), context))
    item = Resource(
        type=kind,
        name=name,
        id=_resource_id(kind, name) if isinstance(name, str) else UNKNOWN,
        location=location,
        raw=resource,
        context=context,
        condition=condition,
        existing=resource.get("existing") is True,
    )
    if kind.lower() != "microsoft.resources/deployments":
        return [item]
    properties = _mapping(resource.get("properties"))
    nested = _mapping(properties.get("template"))
    if not nested or depth >= MAX_DEPTH:
        return [replace(item, linked=True)]
    if _mapping(properties.get("expressionEvaluationOptions")).get("scope") == "inner":
        values = {
            key: evaluate(_mapping(value).get("value", UNKNOWN), context)
            for key, value in _mapping(properties.get("parameters")).items()
        }
        child = _Context(values, nested)
    else:
        child = context
    return [
        item,
        *_resources(nested, child, f"{location}.properties.template", condition, depth + 1),
    ]


@dataclass(frozen=True)
class _Result:
    status: VerificationStatus
    detail: str
    observed: str | None = None


def _combine(results: Sequence[_Result]) -> _Result:
    observed = "; ".join(item.observed for item in results if item.observed) or None
    for status in ("failed", "unknown"):
        selected = [item for item in results if item.status == status]
        if selected:
            return _Result(
                cast(VerificationStatus, status),
                " ".join(item.detail for item in selected),
                observed,
            )
    return _Result("passed", " ".join(item.detail for item in results), observed)


def _value(label: str, value: object, passes: Callable[[object], bool]) -> _Result:
    if value is ABSENT:
        return _Result(
            "unknown", f"{label} is absent; the platform default is not verified.", "absent"
        )
    if value is UNKNOWN:
        return _Result("unknown", f"{label} cannot be resolved from the supplied artefacts.")
    observed = f"{label}={_text(value)}"
    if passes(value):
        return _Result("passed", f"{label} is set as expected.", observed)
    return _Result("failed", f"{label} is explicitly set to {_text(value)}.", observed)


def _tls(value: object) -> bool:
    digits = re.sub(r"[^0-9]", "", str(value))
    return len(digits) >= 2 and (int(digits[0]), int(digits[1])) >= (1, 2)


def _related(
    resources: Sequence[Resource], kind: str, matches: Callable[[Resource], bool]
) -> tuple[list[Resource], list[Resource]]:
    deployed, uncertain = [], []
    for item in resources:
        if item.type.lower() != kind.lower() or item.condition is False or not matches(item):
            continue
        if item.condition is True and not item.existing:
            deployed.append(item)
        else:
            uncertain.append(item)
    return deployed, uncertain


def _in_transit(resource: Resource, _: Sequence[Resource]) -> _Result:
    if resource.type.lower() == "microsoft.storage/storageaccounts":
        return _combine(
            [
                _value(
                    "supportsHttpsTrafficOnly",
                    resource.get("properties.supportsHttpsTrafficOnly"),
                    lambda value: value is True,
                ),
                _value("minimumTlsVersion", resource.get("properties.minimumTlsVersion"), _tls),
            ]
        )
    return _combine(
        [
            _value("httpsOnly", resource.get("properties.httpsOnly"), lambda value: value is True),
            _value("minTlsVersion", resource.get("properties.siteConfig.minTlsVersion"), _tls),
        ]
    )


def _diagnostics(resource: Resource, resources: Sequence[Resource]) -> _Result:
    deployed, uncertain = _related(
        resources,
        "Microsoft.Insights/diagnosticSettings",
        lambda item: item.get("scope") == resource.id,
    )
    if not deployed:
        if uncertain:
            return _Result("unknown", "A diagnostic setting is conditionally deployed or external.")
        return _Result(
            "unknown",
            "No diagnostic setting targets this resource in the supplied artefacts; external "
            "evidence is required.",
            "absent",
        )
    results = []
    for setting in deployed:
        destinations = [
            setting.get(f"properties.{key}")
            for key in ("workspaceId", "storageAccountId", "eventHubAuthorizationRuleId")
        ]
        entries = [
            *_list(setting.get("properties.logs")),
            *_list(setting.get("properties.metrics")),
        ]
        enabled = [_mapping(entry).get("enabled") for entry in entries]
        if not _known(*destinations, *enabled):
            results.append(_Result("unknown", "Diagnostic setting values cannot be resolved."))
        elif not any(isinstance(value, str) and value for value in destinations):
            results.append(_Result("failed", "The diagnostic setting has no destination."))
        elif True not in enabled:
            results.append(
                _Result("failed", "The diagnostic setting disables every log and metric.", "off")
            )
        else:
            return _Result("passed", "An enabled diagnostic setting targets this resource.", "on")
    return _combine(results)


def _private_access(resource: Resource, resources: Sequence[Resource]) -> _Result:
    public = _value(
        "publicNetworkAccess",
        resource.get("properties.publicNetworkAccess"),
        lambda value: str(value).lower() == "disabled",
    )
    if public.status != "passed":
        return public

    def connected(item: Resource) -> bool:
        connections = [
            *_list(item.get("properties.privateLinkServiceConnections")),
            *_list(item.get("properties.manualPrivateLinkServiceConnections")),
        ]
        return any(
            _mapping(_mapping(connection).get("properties")).get("privateLinkServiceId")
            == resource.id
            for connection in connections
        )

    deployed, uncertain = _related(resources, "Microsoft.Network/privateEndpoints", connected)
    if deployed:
        return _Result(
            "passed",
            "Public access is disabled and a private endpoint connects the resource.",
            public.observed,
        )
    return _Result(
        "unknown",
        "The private endpoint is conditional or external."
        if uncertain
        else "Public access is disabled, but no private endpoint is in the supplied "
        "artefacts; external evidence is required.",
        public.observed,
    )


def _authentication(resource: Resource, resources: Sequence[Resource]) -> _Result:
    deployed, uncertain = _related(
        resources,
        "Microsoft.Web/sites/config",
        lambda item: item.name == f"{resource.name}/authsettingsV2",
    )
    if not deployed:
        if uncertain:
            return _Result("unknown", "Authentication settings are conditional or external.")
        return _Result(
            "unknown", "Authentication settings are absent from the supplied artefacts.", "absent"
        )
    config = deployed[0]
    return _combine(
        [
            _value(
                "platform.enabled",
                config.get("properties.platform.enabled"),
                lambda value: value is True,
            ),
            _value(
                "globalValidation.requireAuthentication",
                config.get("properties.globalValidation.requireAuthentication"),
                lambda value: value is True,
            ),
        ]
    )


def _shared_key(resource: Resource, _: Sequence[Resource]) -> _Result:
    return _value(
        "allowSharedKeyAccess",
        resource.get("properties.allowSharedKeyAccess"),
        lambda value: value is False,
    )


def _soft_delete(resource: Resource, _: Sequence[Resource]) -> _Result:
    return _combine(
        [
            _value(
                "deleteRetentionPolicy.enabled",
                resource.get("properties.deleteRetentionPolicy.enabled"),
                lambda value: value is True,
            ),
            _value(
                "deleteRetentionPolicy.days",
                resource.get("properties.deleteRetentionPolicy.days"),
                lambda value: isinstance(value, int) and not isinstance(value, bool) and value >= 7,
            ),
        ]
    )


def _ddos(resource: Resource, _: Sequence[Resource]) -> _Result:
    enabled = _value(
        "enableDdosProtection",
        resource.get("properties.enableDdosProtection"),
        lambda value: value is True,
    )
    if enabled.status != "passed":
        return enabled
    plan = _value(
        "ddosProtectionPlan.id",
        resource.get("properties.ddosProtectionPlan.id"),
        lambda value: isinstance(value, str) and bool(value),
    )
    return _combine([enabled, plan])


@dataclass(frozen=True)
class Check:
    expected: str
    # An empty set means the check applies to any resource type.
    resource_types: frozenset[str]
    evaluate: Callable[[Resource, Sequence[Resource]], _Result]


CHECKS: dict[str, Check] = {
    "encryption.in_transit": Check(
        "HTTPS only with TLS 1.2 or later",
        frozenset({"microsoft.web/sites", "microsoft.storage/storageaccounts"}),
        _in_transit,
    ),
    "logging.diagnostic_settings": Check(
        "an enabled diagnostic setting with a destination targets the resource",
        frozenset(),
        _diagnostics,
    ),
    "network.private_access": Check(
        "public network access disabled and a private endpoint connected",
        frozenset({"microsoft.web/sites", "microsoft.storage/storageaccounts"}),
        _private_access,
    ),
    "network.ddos_protection": Check(
        "DDoS Network Protection enabled with an associated protection plan",
        frozenset({"microsoft.network/virtualnetworks"}),
        _ddos,
    ),
    "identity.require_authentication": Check(
        "platform authentication enabled and required for every request",
        frozenset({"microsoft.web/sites"}),
        _authentication,
    ),
    "identity.shared_key_disabled": Check(
        "shared-key access disabled",
        frozenset({"microsoft.storage/storageaccounts"}),
        _shared_key,
    ),
    "retention.blob_soft_delete": Check(
        "blob soft delete enabled for at least 7 days",
        frozenset({"microsoft.storage/storageaccounts/blobservices"}),
        _soft_delete,
    ),
}


class ArmTemplateAnalyzer:
    version = ANALYZER_VERSION

    def observe(
        self,
        template: Mapping[str, object],
        parameters: Mapping[str, object],
        checks: Sequence[CheckRequest],
        *,
        artifact_sha256: str,
        basis: Literal["planned", "deployed"],
    ) -> tuple[tuple[ImplementationObservation, ...], ...]:
        context = _Context(parameter_values(parameters), template)
        resources = _resources(template, context, "template")
        return tuple(_observe(request, resources, artifact_sha256, basis) for request in checks)


def _observe(
    request: CheckRequest,
    resources: Sequence[Resource],
    artifact_sha256: str,
    basis: Literal["planned", "deployed"],
) -> tuple[ImplementationObservation, ...]:
    check = CHECKS.get(request.check_id)

    def observation(resource_id: str, location: str, result: _Result) -> ImplementationObservation:
        return ImplementationObservation(
            resource_id=resource_id,
            resource_type=request.resource_type,
            check_id=request.check_id,
            check_version=f"{ANALYZER_VERSION}/{request.check_id}@{CHECK_VERSION}",
            status=result.status,
            detail=result.detail,
            expected=check.expected if check else None,
            observed=result.observed,
            artifact_sha256=artifact_sha256,
            location=location,
            basis=basis,
        )

    kind = request.resource_type.lower()
    if check is None or check.resource_types and kind not in check.resource_types:
        return (
            observation(
                request.resource_type,
                "analyzer",
                _Result(
                    "unsupported",
                    f"No verifier supports {request.check_id} for {request.resource_type}.",
                ),
            ),
        )
    found = [
        observation(
            str(item.id) if item.id is not UNKNOWN else item.location,
            item.location,
            _Result(
                "unknown",
                "A linked deployment is not supplied, so its resources are not covered.",
            ),
        )
        for item in resources
        if item.linked and item.condition is not False
    ]
    candidates = [item for item in resources if item.type.lower() == kind]
    if request.resources:
        found.extend(
            observation(
                name,
                "template",
                _Result("unknown", "The designated resource is not in the supplied artefacts."),
            )
            for name in request.resources
            if not any(item.name == name for item in candidates)
        )
        candidates = [item for item in candidates if item.name in request.resources]
    evaluated = 0
    for item in candidates:
        if item.condition is False:
            continue
        evaluated += 1
        if item.problem:
            result = _Result("unknown", item.problem)
        elif item.existing:
            result = _Result(
                "unknown",
                "The resource is existing or shared infrastructure; external evidence is required.",
            )
        elif item.condition is not True:
            result = _Result("unknown", "The resource is conditionally deployed.")
        elif item.id is UNKNOWN:
            result = _Result("unknown", "The resource name cannot be resolved.")
        else:
            result = check.evaluate(item, resources)
        found.append(
            observation(
                str(item.id) if item.id is not UNKNOWN else item.location, item.location, result
            )
        )
    if not evaluated and not request.resources:
        found.append(
            observation(
                request.resource_type,
                "template",
                _Result(
                    "unknown",
                    f"No deployed {request.resource_type} instance is in the supplied "
                    "artefacts; external evidence is required.",
                ),
            )
        )
    return tuple(found)


def parameter_values(parameters: Mapping[str, object]) -> dict[str, object]:
    """Accept ARM parameter files or plain name-to-value objects."""
    values = _mapping(parameters.get("parameters")) if "parameters" in parameters else parameters
    result: dict[str, object] = {}
    for key, value in values.items():
        if key.startswith("$"):
            continue
        entry = _mapping(value)
        if "reference" in entry:
            # Key Vault references are resolved only at deployment time.
            result[key] = UNKNOWN
        elif "value" in entry:
            result[key] = entry["value"]
        else:
            result[key] = value
    return result


def compile_bicep(path: Path, timeout: float = 120) -> bytes:
    """Compile Bicep with the locally installed CLI; compilation never runs template code."""
    if command := shutil.which("bicep"):
        arguments = [command, "build", str(path), "--stdout"]
    elif az := shutil.which("az"):
        arguments = [az, "bicep", "build", "--file", str(path), "--stdout"]
    else:
        raise ValueError("Bicep compilation requires the bicep or az CLI")
    completed = subprocess.run(arguments, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.decode(errors="replace").strip()[:2000])
    return completed.stdout
