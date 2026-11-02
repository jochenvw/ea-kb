import json
from pathlib import Path

from ekb.adapters.implementation.arm import ArmTemplateAnalyzer, parameter_values
from ekb.domain.assessment import CheckRequest

REFERENCE = Path(__file__).parents[1] / "fixtures" / "reference-app" / "infra" / "main.json"


def observe(template, checks, parameters=None):
    requests = [
        CheckRequest(check_id=check, resource_type=kind, resources=tuple(resources))
        for check, kind, *resources in checks
    ]
    return ArmTemplateAnalyzer().observe(
        template, parameters or {}, requests, artifact_sha256="a" * 64, basis="planned"
    )


def site(name, **properties):
    return {"type": "Microsoft.Web/sites", "name": name, "properties": properties}


def statuses(observations):
    return [(item.resource_id, item.status) for item in observations]


def test_copy_loop_observes_every_instance_and_a_failing_member():
    template = {
        "parameters": {"tls": {"type": "array", "defaultValue": ["1.2", "1.0"]}},
        "resources": [
            {
                "type": "Microsoft.Web/sites",
                "name": "[format('app-{0}', copyIndex())]",
                "copy": {"name": "apps", "count": 2},
                "properties": {
                    "httpsOnly": True,
                    "siteConfig": {"minTlsVersion": "[parameters('tls')[copyIndex()]]"},
                },
            }
        ],
    }
    (result,) = observe(template, [("encryption.in_transit", "Microsoft.Web/sites")])
    assert statuses(result) == [
        ("Microsoft.Web/sites/app-0", "passed"),
        ("Microsoft.Web/sites/app-1", "failed"),
    ]
    assert result[1].observed == "httpsOnly=True; minTlsVersion=1.0"
    assert result[1].check_version == "arm-template-v1/encryption.in_transit@1"


def test_absent_property_is_unknown_but_explicit_disable_fails():
    template = {"resources": [site("absent"), site("off", httpsOnly=False)]}
    (result,) = observe(template, [("encryption.in_transit", "Microsoft.Web/sites")])
    assert [item.status for item in result] == ["unknown", "failed"]
    assert "platform default is not verified" in result[0].detail


def test_parameter_files_conditions_and_keyword_text_are_not_evidence():
    template = {
        "parameters": {"secure": {"type": "bool", "defaultValue": False}},
        "resources": [
            {
                "type": "Microsoft.Storage/storageAccounts",
                "name": "store",
                "condition": "[parameters('secure')]",
                "properties": {"allowSharedKeyAccess": False},
            },
            {
                "type": "Microsoft.Storage/storageAccounts",
                "name": "notes",
                "tags": {"note": "allowSharedKeyAccess false, shared key disabled"},
                "properties": {},
            },
        ],
    }
    check = [("identity.shared_key_disabled", "Microsoft.Storage/storageAccounts")]
    assert statuses(observe(template, check)[0]) == [
        ("Microsoft.Storage/storageAccounts/notes", "unknown")
    ]
    parameters = {"$schema": "x", "parameters": {"secure": {"value": True}}}
    assert statuses(observe(template, check, parameters)[0])[0] == (
        "Microsoft.Storage/storageAccounts/store",
        "passed",
    )
    unresolved = {"parameters": {"secure": {"reference": {"keyVault": {"id": "vault"}}}}}
    assert observe(template, check, unresolved)[0][0].detail == (
        "The resource is conditionally deployed."
    )
    assert "secure" in parameter_values(unresolved)


def test_existing_linked_unsupported_and_missing_resources_remain_unknown():
    template = {
        "resources": [
            {**site("shared", httpsOnly=True), "existing": True},
            {
                "type": "Microsoft.Resources/deployments",
                "name": "module",
                "properties": {"templateLink": {"uri": "https://example.invalid/t.json"}},
            },
        ]
    }
    (result, unsupported, missing) = observe(
        template,
        [
            ("encryption.in_transit", "Microsoft.Web/sites"),
            ("encryption.in_transit", "Example/unknown"),
            ("encryption.in_transit", "Microsoft.Web/sites", "absent-site"),
        ],
    )
    assert [item.status for item in result] == ["unknown", "unknown"]
    assert "linked deployment" in result[0].detail and "existing" in result[1].detail
    assert unsupported[0].status == "unsupported"
    assert ("absent-site", "unknown") in statuses(missing)


def test_inline_module_uses_inner_scope_parameters():
    template = {
        "resources": [
            {
                "type": "Microsoft.Resources/deployments",
                "name": "module",
                "properties": {
                    "expressionEvaluationOptions": {"scope": "inner"},
                    "parameters": {"enabled": {"value": True}},
                    "template": {
                        "parameters": {"enabled": {"type": "bool"}},
                        "resources": [
                            {
                                "type": "Microsoft.Storage/storageAccounts/blobServices",
                                "name": "store/default",
                                "properties": {
                                    "deleteRetentionPolicy": {
                                        "enabled": "[parameters('enabled')]",
                                        "days": 3,
                                    }
                                },
                            }
                        ],
                    },
                },
            }
        ]
    }
    (result,) = observe(
        template,
        [("retention.blob_soft_delete", "Microsoft.Storage/storageAccounts/blobServices")],
    )
    assert statuses(result) == [
        ("Microsoft.Storage/storageAccounts/store/blobServices/default", "failed")
    ]


def test_reference_application_profiles_and_regressions():
    template = json.loads(REFERENCE.read_text())
    checks = [
        ("encryption.in_transit", "Microsoft.Web/sites"),
        ("logging.diagnostic_settings", "Microsoft.Web/sites"),
        ("network.private_access", "Microsoft.Web/sites"),
        ("identity.require_authentication", "Microsoft.Web/sites"),
        ("identity.shared_key_disabled", "Microsoft.Storage/storageAccounts"),
        ("retention.blob_soft_delete", "Microsoft.Storage/storageAccounts/blobServices"),
    ]

    def run(**parameters):
        return [item[0].status for item in observe(template, checks, parameters)]

    assert run(profile="restricted") == ["passed"] * 6
    assert run(profile="public")[:3] == ["passed", "passed", "failed"]
    assert run(profile="restricted", enableDiagnostics=False)[1] == "unknown"
    assert run(profile="restricted", publicNetworkAccess="Enabled")[2] == "failed"
    assert run(profile="restricted", deployPrivateEndpoint=False)[2] == "unknown"
    assert run(profile="restricted", requireAuthentication=False)[3] == "unknown"
