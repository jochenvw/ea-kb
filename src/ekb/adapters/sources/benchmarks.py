from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from collections.abc import AsyncIterator
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx

from ekb.adapters.parsers.benchmarks import (
    BENCHMARK_PARSER_VERSION,
    links,
    normalize_benchmark,
)
from ekb.adapters.sources import HTTPConnector
from ekb.domain.benchmarks import (
    BenchmarkAsset,
    BenchmarkEdition,
    BenchmarkManifest,
    BenchmarkRole,
    BenchmarkSyncReport,
    NormalizedBenchmarkDocument,
    PublishedBenchmarkMapping,
)
from ekb.domain.models import FetchResult, SourceItem, SourceRecord, content_hash, now, stable_id
from ekb.observability import tracer
from ekb.ports import ArtifactStore
from ekb.services.retry import retry

LEARN = "https://learn.microsoft.com/en-us/security/benchmark/azure/"
REPOSITORY_TREE = (
    "https://api.github.com/repos/MicrosoftDocs/SecurityBenchmarks/git/trees/master?recursive=1"
)
POLICY_INDEX = "https://learn.microsoft.com/en-us/azure/governance/policy/samples/built-in-policies"
EDITIONS = {
    edition.key: edition
    for edition in (
        BenchmarkEdition(
            key="asb-v2",
            family="ASB",
            publisher_version="v2",
            overview_url=LEARN + "overview-v2",
            directory="azure_security_benchmark_v2",
        ),
        BenchmarkEdition(
            key="mcsb-v1",
            family="MCSB",
            publisher_version="v1",
            overview_url=LEARN + "overview-mcsb-v1",
            directory="mcsb_v1",
        ),
        BenchmarkEdition(
            key="mcsb-v2",
            family="MCSB",
            publisher_version="v2",
            overview_url=LEARN + "overview",
            directory="mcsb_v2",
        ),
    )
}
DOMAIN_PREFIX = {
    "asb-v2": "security-controls-v2-",
    "mcsb-v1": "mcsb-",
    "mcsb-v2": "mcsb-v2-",
}


def _article(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _asset(
    edition: str,
    url: str,
    role: BenchmarkRole,
    *,
    domain: str | None = None,
    definition_id: str | None = None,
) -> BenchmarkAsset:
    markdown = url.startswith("https://learn.microsoft.com/")
    fmt = "xlsx" if url.endswith(".xlsx") else "md" if markdown else "json"
    return BenchmarkAsset(
        id=stable_id("microsoft-security-benchmark", edition, definition_id or url),
        edition=edition,
        url=url,
        download_url=url + "?accept=text/markdown" if markdown else url,
        role=role,
        format=fmt,
        domain=domain,
        definition_id=definition_id,
    )


class MicrosoftLearnBenchmarkConnector(HTTPConnector):
    """Network synchronization is explicit; normal discovery only reads validated cached assets."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        max_bytes: int = 50_000_000,
        *,
        concurrency: int = 4,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__([], max_bytes, client=client, name="microsoft-security-benchmark")
        if not 1 <= concurrency <= 16:
            raise ValueError("Benchmark download concurrency must be between 1 and 16")
        self.artifacts = artifacts
        self.concurrency = concurrency
        self._sync_lock = asyncio.Lock()
        self._checkpoint_lock = asyncio.Lock()
        self._manifest = BenchmarkManifest()

    async def manifest(self) -> BenchmarkManifest:
        try:
            content = await self.artifacts.get_named("manifest.json")
        except FileNotFoundError:
            return BenchmarkManifest()
        manifest = BenchmarkManifest.model_validate_json(content)
        if manifest.format_version != 1:
            raise ValueError("Unsupported benchmark manifest version")
        return manifest

    async def _checkpoint(self, asset: BenchmarkAsset, error: str | None = None) -> None:
        async with self._checkpoint_lock:
            assets = dict(self._manifest.assets)
            failures = dict(self._manifest.failures)
            if error is None:
                assets[str(asset.id)] = asset
                failures.pop(asset.url, None)
            else:
                failures[asset.url] = error
            updated = self._manifest.model_copy(
                update={
                    "assets": assets,
                    "failures": failures,
                    "updated_at": now(),
                }
            )
            await self.artifacts.put_named(
                "manifest.json", updated.model_dump_json(indent=2).encode()
            )
            self._manifest = updated

    async def raw(self, asset: BenchmarkAsset) -> bytes:
        if not asset.raw_key or not asset.sha256:
            raise ValueError("Benchmark source has no validated original")
        raw = await self.artifacts.get_named(asset.raw_key)
        if content_hash(raw) != asset.sha256:
            raise ValueError("Cached benchmark original failed its integrity check")
        return raw

    async def document(self, asset: BenchmarkAsset) -> NormalizedBenchmarkDocument:
        if not asset.normalized_key or not asset.normalized_sha256:
            raise ValueError("Benchmark source has no validated normalized document")
        raw = await self.artifacts.get_named(asset.normalized_key)
        if content_hash(raw) != asset.normalized_sha256:
            raise ValueError("Normalized benchmark document failed its integrity check")
        document = NormalizedBenchmarkDocument.model_validate_json(raw)
        if (
            document.asset_id != asset.id
            or document.content_hash != asset.sha256
            or document.parser_version != asset.parser_version
        ):
            raise ValueError("Normalized benchmark identity does not match its original")
        return document

    async def discover(self) -> AsyncIterator[SourceItem]:
        with tracer.start_as_current_span("benchmark.discover") as span:
            manifest = await self.manifest()
            span.set_attribute("benchmark.documents_discovered", len(manifest.assets))
        if not manifest.assets:
            raise ValueError("Run 'ekb sources sync microsoft-security-benchmark' first")
        for asset in sorted(manifest.assets.values(), key=lambda value: str(value.id)):
            document = await self.document(asset)
            yield SourceItem(
                id=asset.id,
                connector=self.name,
                location=asset.url,
                name=document.title,
                format=asset.format,
                indicator=f"{asset.sha256}:{asset.parser_version}:{asset.normalized_sha256}",
                metadata={
                    "publisher": "Microsoft",
                    "benchmark_edition": asset.edition,
                    "benchmark_asset": asset.model_dump(mode="json"),
                    "publisher_version": document.publication.publisher_version,
                    "metadata_only": asset.role in {"repository_index", "policy_index"},
                    "retrieved_at": asset.retrieved_at.isoformat() if asset.retrieved_at else None,
                },
            )

    async def fetch(self, item: SourceItem, previous: SourceRecord | None) -> FetchResult:
        asset = BenchmarkAsset.model_validate(item.metadata["benchmark_asset"])
        raw = await self.raw(asset)
        if previous and previous.revision_id and previous.item.indicator == item.indicator:
            return FetchResult(content=None, not_modified=True)
        return FetchResult(content=raw, etag=asset.etag, last_modified=asset.last_modified)

    async def _sync_asset(
        self,
        asset: BenchmarkAsset,
        counts: Counter[str],
    ) -> tuple[BenchmarkAsset, NormalizedBenchmarkDocument]:
        previous = self._manifest.assets.get(str(asset.id))
        cached = None
        if previous and previous.download_url == asset.download_url:
            try:
                cached = await self.raw(previous)
            except (OSError, ValueError):
                pass
        item = SourceItem(
            id=asset.id,
            connector=self.name,
            location=asset.download_url,
            name=asset.url,
            format=asset.format,
        )
        record = (
            SourceRecord(
                item=item,
                revision_id=stable_id(asset.id, previous.sha256),
                etag=previous.etag,
                last_modified=previous.last_modified,
            )
            if cached is not None and previous
            else None
        )
        with tracer.start_as_current_span(
            "benchmark.download",
            attributes={"benchmark.edition": asset.edition, "asset.role": asset.role},
        ):
            result = await retry(
                lambda: super(MicrosoftLearnBenchmarkConnector, self).fetch(item, record)
            )
        raw = cached if result.not_modified else result.content
        if raw is None:
            raise ValueError("Conditional download has no validated cached original")
        if not result.not_modified:
            counts["downloaded"] += 1
        if asset.format == "md" and raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            asset = asset.model_copy(update={"format": "html"})
        digest = content_hash(raw)
        document = None
        if (
            previous
            and digest == previous.sha256
            and previous.parser_version == BENCHMARK_PARSER_VERSION
        ):
            try:
                document = await self.document(previous)
            except (OSError, ValueError):
                pass
        if document is None:
            document = await asyncio.to_thread(
                normalize_benchmark, asset, raw, EDITIONS[asset.edition]
            )
            if asset.role == "overview" and not self._domains(asset, document):
                raise ValueError("Overview has no recognizable linked benchmark domain articles")
            if asset.role == "repository_index":
                tree = json.loads(raw)
                if tree.get("truncated") or not isinstance(tree.get("tree"), list):
                    raise ValueError("Official workbook repository index is incomplete")
            if asset.role == "mapping" and not (document.mappings or document.policy_references):
                raise ValueError("Published mapping page contains no identifiable mappings")
            if asset.domain and any(
                control.domain != asset.domain for control in document.controls
            ):
                raise ValueError("Downloaded controls do not match the linked domain")
            if asset.definition_id and (
                not document.policies
                or document.policies[0].definition_id.casefold() != asset.definition_id.casefold()
            ):
                raise ValueError(
                    "Retrieved policy definition ID differs from the published reference"
                )
            if previous and digest == previous.sha256:
                counts["reparsed"] += 1
        else:
            counts["skipped"] += 1
        directory = (
            "mappings/azure_policy"
            if asset.role in {"policy", "policy_index"} or "controls-policy-mapping" in asset.url
            else EDITIONS[asset.edition].directory
        )
        raw_key = f"{directory}/raw/{digest}.{asset.format}"
        normalized_key = (
            f"{directory}/normalized/{asset.id}-{digest[:32]}-{BENCHMARK_PARSER_VERSION[:16]}.json"
        )
        if (
            previous
            and document.parser_version == previous.parser_version
            and digest == previous.sha256
        ):
            # A 304 is usable only after validating the named original.
            if cached is None:
                await self.artifacts.put_named(raw_key, raw)
        else:
            await self.artifacts.put_named(raw_key, raw)
        normalized = document.model_dump_json(indent=2).encode()
        normalized_hash = content_hash(normalized)
        if not previous or previous.normalized_sha256 != normalized_hash:
            await self.artifacts.put_named(normalized_key, normalized)
        else:
            normalized_key = previous.normalized_key or normalized_key
            try:
                await self.document(previous)
            except (OSError, ValueError):
                await self.artifacts.put_named(normalized_key, normalized)
        artifact_key = previous.artifact_key if previous and previous.sha256 == digest else None
        if artifact_key is None:
            artifact_key = await self.artifacts.put(raw)
        updated = asset.model_copy(
            update={
                "sha256": digest,
                "artifact_key": artifact_key,
                "raw_key": raw_key,
                "normalized_key": normalized_key,
                "normalized_sha256": normalized_hash,
                "etag": result.etag,
                "last_modified": result.last_modified,
                "checked_at": now(),
                "retrieved_at": previous.retrieved_at
                if previous and previous.sha256 == digest
                else now(),
                "parser_version": BENCHMARK_PARSER_VERSION,
            }
        )
        if previous is None or previous.normalized_sha256 != normalized_hash:
            published = [
                PublishedBenchmarkMapping(
                    edition=control.edition, control_id=control.control_id, reference=reference
                )
                for control in document.controls
                for reference in control.mappings
            ] + list(document.mappings)
            for name, prefix in (("nist", "nist-"), ("iso", "iso-"), ("cis", "cis-")):
                selected = [
                    mapping.model_dump(mode="json")
                    for mapping in published
                    if mapping.reference.framework.startswith(prefix)
                ]
                if selected:
                    await self.artifacts.put_named(
                        f"mappings/{name}/{asset.id}-{digest}-{BENCHMARK_PARSER_VERSION[:16]}.json",
                        json.dumps(selected, indent=2).encode(),
                    )
        await self._checkpoint(updated)
        return updated, document

    @staticmethod
    def _domains(
        asset: BenchmarkAsset,
        document: NormalizedBenchmarkDocument,
    ) -> list[BenchmarkAsset]:
        found = {}
        for label, target in links(document.text):
            domain = re.search(r"\(([A-Z]{2,3})\)", label)
            url = _article(urljoin(asset.url, target))
            slug = url.removeprefix(LEARN)
            if (
                domain
                and url.startswith(LEARN)
                and slug.startswith(DOMAIN_PREFIX[asset.edition])
                and not (asset.edition == "mcsb-v1" and slug.startswith("mcsb-v2-"))
            ):
                child = _asset(asset.edition, url, "domain", domain=domain[1])
                found[str(child.id)] = child
        return list(found.values())

    async def sync(
        self,
        versions: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> BenchmarkSyncReport:
        versions = versions or list(EDITIONS)
        if unknown := set(versions) - EDITIONS.keys():
            raise ValueError(f"Unknown benchmark editions: {', '.join(sorted(unknown))}")
        domain_filter = {value.upper() for value in domains} if domains else None
        if domain_filter and any(not re.fullmatch(r"[A-Z]{2,3}", value) for value in domain_filter):
            raise ValueError("Domains must be control-domain identifiers such as NS")
        async with self._sync_lock:
            self._manifest = await self.manifest()
            counts: Counter[str] = Counter()
            failures: dict[str, str] = {}
            results: dict[str, tuple[BenchmarkAsset, NormalizedBenchmarkDocument]] = {}

            async def batch(assets: list[BenchmarkAsset]) -> None:
                assets = list({str(asset.id): asset for asset in assets}.values())
                if len(assets) > 256:
                    raise ValueError("Benchmark discovery exceeds the bounded asset limit")
                queue: asyncio.Queue[BenchmarkAsset | None] = asyncio.Queue(self.concurrency * 2)

                async def worker() -> None:
                    while (asset := await queue.get()) is not None:
                        counts["discovered"] += 1
                        try:
                            results[str(asset.id)] = await self._sync_asset(asset, counts)
                        except Exception as error:
                            message = f"{type(error).__name__}: {error}"[:300]
                            failures[asset.url] = message
                            await self._checkpoint(asset, message)
                            if previous := self._manifest.assets.get(str(asset.id)):
                                try:
                                    results[str(asset.id)] = previous, await self.document(previous)
                                except (OSError, ValueError):
                                    pass

                async with asyncio.TaskGroup() as group:
                    for _ in range(self.concurrency):
                        group.create_task(worker())
                    for asset in assets:
                        await queue.put(asset)
                    for _ in range(self.concurrency):
                        await queue.put(None)

            roots = [_asset(key, EDITIONS[key].overview_url, "overview") for key in versions]
            await batch(roots)
            articles = []
            known_domains = set()
            for root in roots:
                if pair := results.get(str(root.id)):
                    children = self._domains(*pair)
                    known_domains.update(child.domain for child in children)
                    articles.extend(
                        child
                        for child in children
                        if not domain_filter or child.domain in domain_filter
                    )
            if (
                domain_filter
                and (unknown_domains := domain_filter - known_domains)
                and not any(root.url in failures for root in roots)
            ):
                raise ValueError(
                    f"No published domain links found for: {', '.join(sorted(unknown_domains))}"
                )
            if "mcsb-v2" in versions:
                articles.extend(
                    [
                        _asset("mcsb-v2", LEARN + "mcsb-v2-cis-controls-mapping", "mapping"),
                        _asset("mcsb-v2", LEARN + "mcsb-v2-controls-policy-mapping", "mapping"),
                    ]
                )
            await batch(articles)

            workbook_roots = [
                root
                for root in roots
                if (pair := results.get(str(root.id)))
                and any(
                    target.startswith("https://github.com/MicrosoftDocs/SecurityBenchmarks")
                    for _, target in links(pair[1].text)
                )
            ]
            if workbook_roots:
                tree_asset = _asset(workbook_roots[0].edition, REPOSITORY_TREE, "repository_index")
                await batch([tree_asset])
                if tree_pair := results.get(str(tree_asset.id)):
                    tree = json.loads(tree_pair[1].text)
                    workbooks = []
                    for entry in tree["tree"]:
                        path = entry["path"]
                        cloud_workbook = re.fullmatch(
                            r"Microsoft Cloud Security Benchmark/"
                            r"Microsoft_cloud_security_benchmark_v([12])(?:\.0)?\.xlsx",
                            path,
                            re.I,
                        )
                        edition = (
                            "asb-v2"
                            if path
                            == "Azure Security Benchmark/2.0/azure-security-benchmark-v2.0.xlsx"
                            else f"mcsb-v{cloud_workbook[1]}"
                            if cloud_workbook
                            else None
                        )
                        if edition is not None and edition in versions:
                            workbooks.append(
                                _asset(
                                    edition,
                                    "https://raw.githubusercontent.com/MicrosoftDocs/SecurityBenchmarks/master/"
                                    + quote(path),
                                    "workbook",
                                )
                            )
                    await batch(workbooks)

            policy_ids = {
                reference.definition_id.casefold(): reference
                for asset, document in results.values()
                if asset.role == "mapping" and "controls-policy-mapping" in asset.url
                for reference in document.policy_references
                if reference.evidence.section.startswith("NS-")
                and (not domain_filter or "NS" in domain_filter)
            }
            if policy_ids:
                index = _asset("mcsb-v2", POLICY_INDEX, "policy_index")
                await batch([index])
                definitions = []
                resolved = set()
                if pair := results.get(str(index.id)):
                    for published in pair[1].policy_references:
                        identifier = published.definition_id.casefold()
                        if reference := policy_ids.get(identifier):
                            for _, target in links(published.evidence.excerpt):
                                prefix = "https://github.com/Azure/azure-policy/blob/"
                                if (
                                    target.startswith(prefix)
                                    and target.endswith(".json")
                                    and "/built-in-policies/policyDefinitions/" in target
                                    and ".." not in unquote(urlsplit(target).path).split("/")
                                ):
                                    target = target.replace(
                                        prefix,
                                        "https://raw.githubusercontent.com/Azure/azure-policy/",
                                        1,
                                    )
                                    definitions.append(
                                        _asset(
                                            "mcsb-v2",
                                            target,
                                            "policy",
                                            definition_id=reference.definition_id,
                                        )
                                    )
                                    resolved.add(identifier)
                for identifier in policy_ids.keys() - resolved:
                    failures[policy_ids[identifier].url] = (
                        "No actual policy JSON link in the official catalog"
                    )
                await batch(definitions)

            controls: dict[str, set[str]] = {}
            for asset, document in results.values():
                for control in document.controls:
                    if not domain_filter or control.domain in domain_filter:
                        controls.setdefault(asset.edition, set()).add(control.control_id)
            notes = (
                "Only workbooks present in the official repository index are downloaded.",
                "Workbook publication maturity is assessed independently from web editions.",
                "Published mappings imply neither equivalence, full coverage, nor adoption.",
            )
            saved_failures = {**self._manifest.failures, **failures}
            for identifier in policy_ids.keys() - {
                key for key in policy_ids if policy_ids[key].url in failures
            }:
                saved_failures.pop(policy_ids[identifier].url, None)
            self._manifest = self._manifest.model_copy(
                update={
                    "notes": notes,
                    "failures": saved_failures,
                }
            )
            async with self._checkpoint_lock:
                await self.artifacts.put_named(
                    "manifest.json",
                    self._manifest.model_dump_json(indent=2).encode(),
                )
            return BenchmarkSyncReport(
                discovered=counts["discovered"],
                downloaded=counts["downloaded"],
                skipped=counts["skipped"],
                reparsed=counts["reparsed"],
                failed=len(failures),
                controls={key: len(value) for key, value in controls.items()},
                mappings=sum(
                    len(document.mappings)
                    + sum(len(control.mappings) for control in document.controls)
                    for _, document in results.values()
                ),
                policies=sum(len(document.policies) for _, document in results.values()),
                failures=failures,
                notes=notes,
            )
