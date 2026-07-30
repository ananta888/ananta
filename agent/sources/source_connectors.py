"""Narrow source connector ports and the Hub-owned connector registry."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.source_cache import SourceCache
from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.sources.wikimedia_downloader import WikimediaDownloader


_CONNECTOR_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SourceConnectorError(ValueError):
    def __init__(self, reason_code: str, *, detail: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class SourceRevisionResolution:
    revision_digest: str
    immutable_ref: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SourceInventory:
    item_count: int
    total_bytes: int
    exclusions: tuple[Mapping[str, Any], ...]
    manifest_digest: str


@dataclass(frozen=True)
class ConnectorHealth:
    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ConnectorRefreshRequest:
    dry_run: bool = False
    corpus_url: str | None = None
    destination_name: str | None = None


class SourceValidator(Protocol):
    def validate(self, descriptor: Mapping[str, Any]) -> tuple[str, ...]: ...


class SourceRevisionResolver(Protocol):
    def resolve_revision(
        self,
        descriptor: Mapping[str, Any],
    ) -> SourceRevisionResolution: ...


class SourceInventoryProvider(Protocol):
    def inventory(self, descriptor: Mapping[str, Any]) -> SourceInventory: ...


class SourceRefresher(Protocol):
    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]: ...


class SourceHealthProvider(Protocol):
    def health(self, descriptor: Mapping[str, Any]) -> ConnectorHealth: ...


@dataclass(frozen=True)
class SourceConnector:
    connector_type: str
    validator: SourceValidator
    revision_resolver: SourceRevisionResolver
    inventory_provider: SourceInventoryProvider
    refresher: SourceRefresher
    health_provider: SourceHealthProvider


class ConnectorRegistry:
    """Select connectors by canonical type without concrete type branching."""

    def __init__(self, connectors: Sequence[SourceConnector] = ()) -> None:
        self._connectors: dict[str, SourceConnector] = {}
        for connector in connectors:
            self.register(connector)

    @staticmethod
    def canonical_type(value: str) -> str:
        connector_type = str(value or "").strip().lower()
        if not _CONNECTOR_TYPE_PATTERN.fullmatch(connector_type):
            raise SourceConnectorError("invalid_connector_type")
        return connector_type

    def register(self, connector: SourceConnector) -> None:
        connector_type = self.canonical_type(connector.connector_type)
        if connector_type in self._connectors:
            raise SourceConnectorError("connector_already_registered")
        self._connectors[connector_type] = connector

    def get(self, connector_type: str) -> SourceConnector:
        canonical = self.canonical_type(connector_type)
        connector = self._connectors.get(canonical)
        if connector is None:
            raise SourceConnectorError("unsupported_source_type")
        return connector

    def list_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._connectors))


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DescriptorConnectorAdapter:
    """Passive adapter for imported or inline sources without remote refresh."""

    def __init__(self, connector_type: str) -> None:
        self.connector_type = ConnectorRegistry.canonical_type(connector_type)

    def validate(self, descriptor: Mapping[str, Any]) -> tuple[str, ...]:
        errors: list[str] = []
        if not str(descriptor.get("source_id") or "").strip():
            errors.append("source_id_required")
        if str(descriptor.get("source_type") or "").strip().lower() != self.connector_type:
            errors.append("connector_type_mismatch")
        return tuple(errors)

    def resolve_revision(
        self,
        descriptor: Mapping[str, Any],
    ) -> SourceRevisionResolution:
        digest = _canonical_digest(descriptor)
        return SourceRevisionResolution(
            revision_digest=digest,
            immutable_ref=f"descriptor-sha256:{digest}",
            metadata={"connector_type": self.connector_type},
        )

    def inventory(self, descriptor: Mapping[str, Any]) -> SourceInventory:
        digest = _canonical_digest(descriptor)
        return SourceInventory(
            item_count=0,
            total_bytes=0,
            exclusions=(),
            manifest_digest=digest,
        )

    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]:
        return {
            "source_id": str(descriptor.get("source_id") or ""),
            "status": "planned" if request.dry_run else "skipped",
            "reason_code": (
                "dry_run"
                if request.dry_run
                else "connector_does_not_support_remote_refresh"
            ),
        }

    def health(self, descriptor: Mapping[str, Any]) -> ConnectorHealth:
        errors = self.validate(descriptor)
        return ConnectorHealth(
            status="healthy" if not errors else "degraded",
            reason_code=errors[0] if errors else None,
        )

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_type=self.connector_type,
            validator=self,
            revision_resolver=self,
            inventory_provider=self,
            refresher=self,
            health_provider=self,
        )


class KeycloakConnectorAdapter(DescriptorConnectorAdapter):
    def __init__(
        self,
        *,
        fetcher: KeycloakDocsFetcher,
        cache: SourceCache,
        snapshots: SourceSnapshotStore,
    ) -> None:
        super().__init__("keycloak_docs")
        self._fetcher = fetcher
        self._cache = cache
        self._snapshots = snapshots

    def inventory(self, descriptor: Mapping[str, Any]) -> SourceInventory:
        fetch_source = dict(descriptor.get("fetch_source") or {})
        urls = [
            str(fetch_source.get("url") or ""),
            *[
                str(item)
                for item in list(fetch_source.get("additional_urls") or [])
            ],
        ]
        digest = _canonical_digest([url for url in urls if url])
        return SourceInventory(
            item_count=len([url for url in urls if url]),
            total_bytes=0,
            exclusions=(),
            manifest_digest=digest,
        )

    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]:
        source_id = str(descriptor.get("source_id") or "")
        if request.dry_run:
            return {
                "source_id": source_id,
                "status": "planned",
                "reason_code": "dry_run",
            }
        report = self._fetcher.fetch(descriptor=dict(descriptor), dry_run=False)
        pages = list(report.get("pages") or [])
        for page in pages:
            self._cache.put_raw(
                source_id=source_id,
                payload=str(page.get("raw_html") or ""),
            )
            self._cache.put_extracted(
                source_id=source_id,
                payload=str(page.get("extracted_text") or ""),
            )
        self._snapshots.mark_superseded(
            source_id=source_id,
            keep_snapshot_id=str(report["snapshot"]["snapshot_id"]),
        )
        return {"source_id": source_id, "status": "ok", "report": report}


class WikimediaConnectorAdapter(DescriptorConnectorAdapter):
    def __init__(
        self,
        *,
        downloader: WikimediaDownloader,
        cache: SourceCache,
        snapshots: SourceSnapshotStore,
        destination_root: Path = Path("data/wiki_corpora"),
    ) -> None:
        super().__init__("wikimedia_dump")
        self._downloader = downloader
        self._cache = cache
        self._snapshots = snapshots
        self._destination_root = destination_root

    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]:
        source_id = str(descriptor.get("source_id") or "")
        if request.dry_run:
            return {
                "source_id": source_id,
                "status": "planned",
                "reason_code": "dry_run",
            }
        if not request.corpus_url or not request.destination_name:
            return {
                "source_id": source_id,
                "status": "queued",
                "reason_code": "download_parameters_required",
                "human_message": (
                    "Provide corpus_url and destination_name for dump refresh"
                ),
            }
        destination_name = Path(request.destination_name)
        if (
            destination_name.is_absolute()
            or len(destination_name.parts) != 1
            or destination_name.name in {"", ".", ".."}
        ):
            raise SourceConnectorError("invalid_destination_name")
        descriptor_hash = str(
            (descriptor.get("extensions") or {}).get("descriptor_hash")
            or _canonical_digest(descriptor)
        )
        destination = self._destination_root / destination_name.name
        report = self._downloader.download(
            source_id=source_id,
            descriptor_hash=descriptor_hash,
            url=request.corpus_url,
            destination=destination,
            max_parallel=1,
        )
        self._snapshots.mark_superseded(
            source_id=source_id,
            keep_snapshot_id=str(report["snapshot"]["snapshot_id"]),
        )
        return {"source_id": source_id, "status": "ok", "report": report}


def build_default_connector_registry(
    *,
    keycloak_fetcher: KeycloakDocsFetcher,
    wikimedia_downloader: WikimediaDownloader,
    cache: SourceCache,
    snapshots: SourceSnapshotStore,
    additional_connectors: Sequence[SourceConnector] = (),
) -> ConnectorRegistry:
    connectors = [
        KeycloakConnectorAdapter(
            fetcher=keycloak_fetcher,
            cache=cache,
            snapshots=snapshots,
        ).connector(),
        WikimediaConnectorAdapter(
            downloader=wikimedia_downloader,
            cache=cache,
            snapshots=snapshots,
        ).connector(),
    ]
    connectors.extend(
        DescriptorConnectorAdapter(connector_type).connector()
        for connector_type in (
            "open_notebook",
            "open_notebook_export",
            "text",
            "inline_text",
            "wiki",
        )
    )
    connectors.extend(additional_connectors)
    return ConnectorRegistry(connectors)
