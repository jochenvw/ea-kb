import asyncio
import io
import json
from collections import Counter

import httpx
import pytest
from openpyxl import Workbook

import ekb.adapters.parsers.benchmarks as parser_module
import ekb.adapters.sources.benchmarks as connector_module
from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.parsers.benchmarks import normalize_benchmark
from ekb.adapters.sources.benchmarks import (
    EDITIONS,
    LEARN,
    MicrosoftLearnBenchmarkConnector,
    _asset,
)
from ekb.domain.models import PublicationStatus, content_hash, fingerprint

V1 = """# Synthetic benchmark

## NS-6: Protect applications

| CIS Controls v8 | NIST SP 800-53 r4 |
| --- | --- |
| 13.10 | SC-7 |

**Security principle**: Protect applications with a firewall.

**Azure guidance**: Azure Application Gateway, Azure Front Door, and Azure CDN offer WAF.

Choose detection or prevention mode.

**AWS guidance**: AWS-specific guidance must remain outside Azure guidance.
"""
V2 = """# Synthetic benchmark

## NS-6: Protect applications

### Security principle

Protect applications with a firewall and inspect HTTP traffic.

### Risk to mitigate

Application attacks.

### NS-6.1: Configure a firewall

Choose Azure Application Gateway, Azure Front Door, or Azure Content Delivery Network (Azure CDN).

### Implementation example

Configure inspection on the selected Azure service.

### Criticality level

Must have.

### Control mapping

- **NIST SP800-53 Rev.5:** SC-7, SC-7(5), SI-10
- **ISO 27001:2022:** A.8.20
- **CIS Controls v8.1:** 13.2, 13.9
- **NIST CSF v2.0:** PR.AC-05
"""


def test_structured_controls_preserve_revisions_evidence_and_product_choices():
    documents = [
        normalize_benchmark(
            _asset(key, LEARN + slug, "domain", domain="NS"), text.encode(), EDITIONS[key]
        )
        for key, slug, text in (
            ("mcsb-v1", "mcsb-network-security", V1),
            ("mcsb-v2", "mcsb-v2-network-security", V2),
        )
    ]
    old, new = (document.controls[0] for document in documents)
    assert old.control_id == new.control_id == "NS-6"
    assert old.security_principle != new.security_principle
    assert "AWS-specific" not in old.azure_guidance
    assert old.implementation_options_are_alternatives is None
    assert new.implementation_options_are_alternatives is True
    assert len(new.implementation_options) == 3
    assert "Configure inspection" in new.azure_guidance
    assert new.subcontrols[0].control_id == "NS-6.1"
    assert new.criticality == "Must have."
    assert {
        (ref.revision, ref.control_id) for ref in old.mappings if ref.framework == "nist-sp-800-53"
    } == {("4", "SC-7")}
    assert {
        (ref.revision, ref.control_id) for ref in new.mappings if ref.framework == "nist-sp-800-53"
    } == {("5", "SC-7"), ("5", "SC-7(5)"), ("5", "SI-10")}
    assert all(ref.resolution == "unresolved" for ref in new.mappings)
    assert any(ref.framework == "nist-csf" and ref.control_id == "PR.AC-05" for ref in new.mappings)
    for document in documents:
        for control in document.controls:
            passages = [control.evidence, *[ref.evidence for ref in control.mappings]]
            if control.principle_evidence:
                passages.append(control.principle_evidence)
            passages.extend(subcontrol.evidence for subcontrol in control.subcontrols)
            for passage in passages:
                assert document.text[passage.start : passage.end] == passage.excerpt
                assert passage.source_id == document.asset_id
                assert passage.content_hash == document.content_hash


@pytest.mark.parametrize("historical", [False, True])
def test_official_workbook_layouts_preserve_workbook_specific_maturity(historical):
    book = Workbook()
    readme = book.active
    assert readme is not None
    readme.title = "Readme"
    readme.append(["About this workbook"])
    readme.append(["This spreadsheet is designed to provide a private preview version."])
    sheet = book.create_sheet("Network Security")
    if historical:
        sheet.append(
            [
                "Azure Control",
                "Azure ID",
                "NIST SP800-53 r4 ID(s)",
                "Benchmark Recommendation",
                "Generic Guidance",
            ]
        )
        sheet.append(
            [
                "Network Security",
                "NS-6",
                "SC-7: Boundary protection",
                "Use a firewall",
                "Configure inspection.",
            ]
        )
        edition = "asb-v2"
    else:
        sheet.append(
            [
                "ID",
                "Control Domain",
                "NIST SP800-53 r4 ID(s)",
                "Recommendation",
                "Security Principle",
                "Azure Guidance",
            ]
        )
        sheet.append(
            [
                "NS-6",
                "Network Security",
                "SC-7: Boundary protection",
                "Use a firewall",
                "Protect applications.",
                "Configure Azure WAF.",
            ]
        )
        edition = "mcsb-v1"
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    asset = _asset(
        edition,
        "https://raw.githubusercontent.com/MicrosoftDocs/SecurityBenchmarks/master/example.xlsx",
        "workbook",
    )
    document = normalize_benchmark(asset, buffer.getvalue(), EDITIONS[edition])
    control = document.controls[0]
    assert control.control_id == "NS-6"
    assert control.mappings[0].revision == "4"
    assert control.mappings[0].control_id == "SC-7"
    assert document.publication.publication_status == PublicationStatus.PREVIEW
    assert "Worksheet: Network Security; row: 2" in control.evidence.excerpt
    if historical:
        assert control.security_principle is None
        assert control.generic_guidance == "Configure inspection."
    evidence = next(
        item for item in document.publication.evidence if "private preview" in item.excerpt
    )
    assert evidence.start is not None and evidence.end is not None
    assert document.text[evidence.start : evidence.end] == evidence.excerpt


def test_policy_definition_version_and_resource_scope_are_not_benchmark_versions():
    identifier = (
        "/providers/Microsoft.Authorization/policyDefinitions/564feb30-bf6a-4854-b4bb-0d2d2d1e6c66"
    )
    raw = json.dumps(
        {
            "id": identifier,
            "properties": {
                "displayName": "Preview: Synthetic WAF policy",
                "mode": "Indexed",
                "metadata": {"version": "2.0.0", "category": "Network"},
                "policyRule": {
                    "if": {"field": "type", "equals": "Microsoft.Network/applicationGateways"},
                    "then": {"effect": "Audit"},
                },
            },
        }
    ).encode()
    document = normalize_benchmark(
        _asset(
            "mcsb-v2",
            "https://raw.githubusercontent.com/Azure/azure-policy/master/policy.json",
            "policy",
            definition_id=identifier,
        ),
        raw,
        EDITIONS["mcsb-v2"],
    )
    assert document.policies[0].version == "2.0.0"
    assert document.policies[0].resource_types == ("Microsoft.Network/applicationGateways",)
    assert document.publication.publisher_version == "2.0.0"
    assert document.publication.publication_status == PublicationStatus.PREVIEW
    assert document.title == "Preview: Synthetic WAF policy"
    assert not document.controls


async def test_sync_resumes_checks_cache_replays_parser_and_discovers_without_network(
    tmp_path, monkeypatch
):
    requests = []
    fail_domain = True
    active = maximum = 0

    async def handle(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0)
            requests.append(request)
            if request.url.path.endswith("overview-mcsb-v1"):
                raw = "# Synthetic benchmark\n\n[Network security (NS)](mcsb-network-security)"
            elif request.url.path.endswith("mcsb-network-security"):
                if fail_domain:
                    return httpx.Response(404)
                raw = V1
            else:
                raise AssertionError(f"Unbounded discovery: {request.url}")
            if request.headers.get("if-none-match") == '"source-1"':
                return httpx.Response(304)
            return httpx.Response(200, text=raw, headers={"etag": '"source-1"'})
        finally:
            active -= 1

    store = FilesystemArtifactStore(
        tmp_path / "artifacts", document_directory=tmp_path / "benchmark"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = MicrosoftLearnBenchmarkConnector(store, concurrency=2, client=client)
        first = await connector.sync(["mcsb-v1"], ["NS"])
        assert first.failed == 1
        assert len((await connector.manifest()).assets) == 1
        fail_domain = False
        resumed = MicrosoftLearnBenchmarkConnector(store, concurrency=2, client=client)
        second = await resumed.sync(["mcsb-v1"], ["NS"])
        assert second.failed == 0 and second.skipped == 1
        assert second.controls == {"mcsb-v1": 1}
        assert not (await resumed.manifest()).failures
        before = len(requests)
        items = [item async for item in resumed.discover()]
        domain = next(
            item for item in items if item.metadata["benchmark_asset"]["role"] == "domain"
        )
        assert (await resumed.fetch(domain, None)).content == V1.encode()
        assert len(requests) == before
        previous = await resumed.manifest()
        version = fingerprint(implementation="synthetic-parser-replay")
        monkeypatch.setattr(connector_module, "BENCHMARK_PARSER_VERSION", version)
        monkeypatch.setattr(parser_module, "BENCHMARK_PARSER_VERSION", version)
        replay = await resumed.sync(["mcsb-v1"], ["NS"])
        assert replay.reparsed == 2 and replay.downloaded == 0
        after = await resumed.manifest()
        assert set(previous.assets) == set(after.assets)
        assert all(asset.parser_version == version for asset in after.assets.values())
        unchanged = await resumed.sync(["mcsb-v1"], ["NS"])
        assert unchanged.skipped == 2 and unchanged.reparsed == 0
        asset = next(asset for asset in after.assets.values() if asset.role == "domain")
        assert asset.raw_key
        await store.put_named(asset.raw_key, b"tampered")
        repaired = await resumed.sync(["mcsb-v1"], ["NS"])
        assert repaired.failed == 0
        assert await resumed.raw(asset) == V1.encode()
        assert "if-none-match" not in requests[-1].headers
        assert maximum <= 2
        assert Counter(asset.role for asset in after.assets.values()) == {
            "overview": 1,
            "domain": 1,
        }
        assert await store.get(content_hash(V1.encode())) == V1.encode()


async def test_html_fallback_and_focused_sync_preserve_other_editions(tmp_path):
    def handle(request):
        slug = request.url.path.rsplit("/", 1)[-1]
        if slug == "overview-v2":
            raw = b"""<html><main><h1>Azure Security Benchmark</h1>
<p><a href="security-controls-v2-network-security">Network security (NS)</a></p></main></html>"""
        elif slug == "security-controls-v2-network-security":
            raw = b"""<html><main><h2>NS-6: Protect applications</h2>
<p><strong>Security principle</strong>: Use a firewall.</p></main></html>"""
        elif slug == "overview-mcsb-v1":
            raw = b"# Benchmark\n\n[Network security (NS)](mcsb-network-security)"
        elif slug == "mcsb-network-security":
            raw = V1.encode()
        else:
            raise AssertionError(str(request.url))
        return httpx.Response(200, content=raw)

    store = FilesystemArtifactStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = MicrosoftLearnBenchmarkConnector(store, client=client)
        report = await connector.sync(["asb-v2"], ["NS"])
        assert report.failed == 0, report.failures
        assert (await connector.sync(["mcsb-v1"], ["NS"])).failed == 0
        manifest = await connector.manifest()
        assert len(manifest.assets) == 4
        assert {asset.edition for asset in manifest.assets.values()} == {"asb-v2", "mcsb-v1"}
        html = next(
            asset
            for asset in manifest.assets.values()
            if asset.edition == "asb-v2" and asset.role == "domain"
        )
        assert html.format == "html"
        assert (await connector.document(html)).controls[0].security_principle == "Use a firewall."


async def test_policy_links_are_resolved_to_actual_matching_json_with_bounded_downloads(tmp_path):
    identifiers = [
        f"/providers/Microsoft.Authorization/policyDefinitions/{index:08d}-bf6a-4854-b4bb-0d2d2d1e6c66"
        for index in range(3)
    ]
    active = maximum = 0
    portal = "https://portal.azure.com/#definitionId/"
    repository = (
        "https://github.com/Azure/azure-policy/blob/master/"
        "built-in-policies/policyDefinitions/Network/"
    )

    async def handle(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0)
            slug = request.url.path.rsplit("/", 1)[-1]
            if slug == "overview":
                raw = "# MCSB v2 (preview)\n\n" + "\n".join(
                    f"[Network security (NS)](mcsb-v2-network-security-{index})"
                    for index in range(3)
                )
            elif slug.startswith("mcsb-v2-network-security-"):
                raw = V2.replace("NS-6", f"NS-{6 + int(slug[-1])}")
            elif slug == "mcsb-v2-cis-controls-mapping":
                raw = "| MCSB Control | CIS Controls v8.1 |\n| --- | --- |\n| NS-6 | 13.2, 13.9 |"
            elif slug == "mcsb-v2-controls-policy-mapping":
                raw = "## NS-6: Protect applications\n\n" + "\n".join(
                    f"| [Policy {index}]({portal}{identifier}) | Description | Audit | 2.0.0 |"
                    for index, identifier in enumerate(identifiers)
                )
            elif slug == "built-in-policies":
                raw = "\n".join(
                    f"| [Policy]({portal}{identifier}) | [2.0.0]({repository}{index}.json) |"
                    for index, identifier in enumerate(identifiers)
                )
            elif slug.endswith(".json"):
                identifier = identifiers[int(slug.removesuffix(".json"))]
                return httpx.Response(
                    200,
                    json={
                        "id": identifier,
                        "properties": {
                            "displayName": "Synthetic policy",
                            "metadata": {"version": "2.0.0"},
                        },
                    },
                )
            else:
                raise AssertionError(str(request.url))
            return httpx.Response(200, text=raw)
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = MicrosoftLearnBenchmarkConnector(
            FilesystemArtifactStore(tmp_path),
            concurrency=2,
            client=client,
        )
        report = await connector.sync(["mcsb-v2"], ["NS"])
        assert report.failed == 0, report.failures
        assert report.policies == 3 and maximum <= 2
        documents = [
            await connector.document(asset)
            for asset in (await connector.manifest()).assets.values()
            if asset.role == "policy"
        ]
        assert {document.policies[0].definition_id for document in documents} == set(identifiers)
