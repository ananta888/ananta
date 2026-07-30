from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.source_admission_gate import (
    SourceAdmissionGatePort,
    SourceIndexAdmissionRequest,
)
from agent.sources.source_cache import SourceCache
from agent.sources.source_connectors import (
    ConnectorHealth,
    ConnectorRefreshRequest,
    ConnectorRegistry,
    SourceConnector,
    SourceConnectorError,
    SourceInventory,
    SourceRevisionResolution,
    build_default_connector_registry,
)
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.sources.wikimedia_downloader import WikimediaDownloader
from agent.services.source_admission_service import (
    SourceAdmissionDecision,
    SourceInventoryEvidence,
    SourceScanEvidence,
)


def _parse_interval(value: str) -> timedelta:
    raw = str(value or "").strip().lower()
    if not raw:
        return timedelta(hours=24)
    try:
        if raw.endswith("m"):
            return timedelta(minutes=int(raw[:-1]))
        if raw.endswith("h"):
            return timedelta(hours=int(raw[:-1]))
        if raw.endswith("d"):
            return timedelta(days=int(raw[:-1]))
    except ValueError:
        return timedelta(hours=24)
    return timedelta(hours=24)


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


class SourceRefreshService:
    def __init__(
        self,
        *,
        registry: SourceRegistry | None = None,
        snapshots: SourceSnapshotStore | None = None,
        cache: SourceCache | None = None,
        keycloak_fetcher: KeycloakDocsFetcher | None = None,
        wikimedia_downloader: WikimediaDownloader | None = None,
        connector_registry: ConnectorRegistry | None = None,
        additional_connectors: Sequence[SourceConnector] = (),
        admission_gate: SourceAdmissionGatePort | None = None,
    ) -> None:
        self.registry = registry or SourceRegistry()
        self.snapshots = snapshots or SourceSnapshotStore()
        self.cache = cache or SourceCache()
        self.keycloak_fetcher = keycloak_fetcher or KeycloakDocsFetcher(snapshot_store=self.snapshots)
        self.wikimedia_downloader = wikimedia_downloader or WikimediaDownloader(snapshot_store=self.snapshots)
        if connector_registry is None:
            self.connector_registry = build_default_connector_registry(
                keycloak_fetcher=self.keycloak_fetcher,
                wikimedia_downloader=self.wikimedia_downloader,
                cache=self.cache,
                snapshots=self.snapshots,
                additional_connectors=additional_connectors,
            )
        else:
            self.connector_registry = connector_registry
            for connector in additional_connectors:
                self.connector_registry.register(connector)
        self._admission_gate = admission_gate

    def is_due(self, descriptor: dict[str, Any]) -> bool:
        source_id = str(descriptor.get("source_id") or "")
        latest = self.snapshots.latest_indexed_snapshot(source_id=source_id)
        if latest is None:
            return True
        refresh_interval = str((descriptor.get("fetch_source") or {}).get("refresh_interval") or "24h")
        due_after = _parse_interval(refresh_interval)
        retrieved_at = _parse_iso(str(latest.get("retrieved_at") or latest.get("created_at") or ""))
        if retrieved_at is None:
            return True
        return datetime.now(UTC) >= retrieved_at + due_after

    def plan_due_sources(self) -> list[dict[str, Any]]:
        plans: list[dict[str, Any]] = []
        for descriptor in self.registry.list_sources(include_disabled=True):
            source_id = str(descriptor.get("source_id") or "")
            enabled = bool(descriptor.get("enabled", True))
            if not enabled:
                plans.append({"source_id": source_id, "action": "skip", "reason_code": "source_disabled"})
                continue
            if self.is_due(descriptor):
                plans.append({"source_id": source_id, "action": "refresh"})
            else:
                plans.append({"source_id": source_id, "action": "skip", "reason_code": "not_due"})
        return plans

    def refresh_source(
        self,
        *,
        source_id: str,
        dry_run: bool = False,
        corpus_url: str | None = None,
        destination_name: str | None = None,
    ) -> dict[str, Any]:
        descriptor, connector = self._resolve_connector(source_id)
        if not bool(descriptor.get("enabled", True)):
            return {"source_id": source_id, "status": "skipped", "reason_code": "source_disabled"}
        try:
            errors = connector.validator.validate(descriptor)
            if errors:
                return {
                    "source_id": source_id,
                    "status": "failed",
                    "reason_code": "descriptor_invalid",
                    "validation_errors": list(errors),
                }
            return dict(
                connector.refresher.refresh(
                    descriptor,
                    ConnectorRefreshRequest(
                        dry_run=dry_run,
                        corpus_url=corpus_url,
                        destination_name=destination_name,
                    ),
                )
            )
        except SourceConnectorError as exc:
            return {
                "source_id": source_id,
                "status": "failed",
                "reason_code": exc.reason_code,
            }

    def resolve_revision(self, *, source_id: str) -> SourceRevisionResolution:
        descriptor, connector = self._resolve_connector(source_id)
        self._require_enabled(descriptor)
        self._require_valid(connector, descriptor)
        return connector.revision_resolver.resolve_revision(descriptor)

    def inventory(self, *, source_id: str) -> SourceInventory:
        descriptor, connector = self._resolve_connector(source_id)
        self._require_enabled(descriptor)
        self._require_valid(connector, descriptor)
        return connector.inventory_provider.inventory(descriptor)

    def health(self, *, source_id: str) -> ConnectorHealth:
        descriptor, connector = self._resolve_connector(source_id)
        if not bool(descriptor.get("enabled", True)):
            return ConnectorHealth(
                status="degraded",
                reason_code="source_disabled",
            )
        return connector.health_provider.health(descriptor)

    def authorize_index_release(
        self,
        *,
        source_id: str,
        source_revision_id: str,
        policy_digest: str,
        inventory_evidence: SourceInventoryEvidence,
        scan_evidence: SourceScanEvidence,
    ) -> SourceAdmissionDecision:
        """Fail closed unless exact connector evidence passes the Hub gate."""

        if self._admission_gate is None:
            raise SourceConnectorError("source_admission_gate_required")
        descriptor, connector = self._resolve_connector(source_id)
        self._require_enabled(descriptor)
        self._require_valid(connector, descriptor)
        revision = connector.revision_resolver.resolve_revision(descriptor)
        inventory = connector.inventory_provider.inventory(descriptor)
        if (
            inventory_evidence.revision_digest != revision.revision_digest
            or inventory_evidence.manifest_digest != inventory.manifest_digest
            or inventory_evidence.file_count != inventory.item_count
            or inventory_evidence.total_bytes != inventory.total_bytes
            or scan_evidence.revision_digest != revision.revision_digest
            or scan_evidence.manifest_digest != inventory.manifest_digest
        ):
            raise SourceConnectorError("source_admission_evidence_mismatch")
        return self._admission_gate.require_admitted(
            SourceIndexAdmissionRequest(
                tenant_id=str(descriptor.get("tenant_id") or "").strip(),
                project_id=str(descriptor.get("project_id") or "").strip(),
                source_revision_id=source_revision_id,
                revision_digest=revision.revision_digest,
                policy_digest=policy_digest,
                inventory=inventory_evidence,
                scan=scan_evidence,
            )
        )

    def refresh_due_sources(self, *, dry_run: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.plan_due_sources():
            source_id = str(item.get("source_id") or "")
            action = str(item.get("action") or "")
            if action != "refresh":
                results.append({"source_id": source_id, "status": "skipped", "reason_code": str(item.get("reason_code") or "")})
                continue
            results.append(self.refresh_source(source_id=source_id, dry_run=dry_run))
        return results

    def _resolve_connector(
        self,
        source_id: str,
    ) -> tuple[dict[str, Any], SourceConnector]:
        descriptor = self.registry.get_source(source_id)
        if descriptor is None:
            raise ValueError("source_not_found")
        source_type = str(descriptor.get("source_type") or "")
        return descriptor, self.connector_registry.get(source_type)

    @staticmethod
    def _require_enabled(descriptor: dict[str, Any]) -> None:
        if not bool(descriptor.get("enabled", True)):
            raise SourceConnectorError("source_disabled")

    @staticmethod
    def _require_valid(
        connector: SourceConnector,
        descriptor: dict[str, Any],
    ) -> None:
        errors = connector.validator.validate(descriptor)
        if errors:
            raise SourceConnectorError(
                "descriptor_invalid",
                detail=",".join(errors),
            )
