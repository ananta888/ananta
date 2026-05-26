from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.source_cache import SourceCache
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.sources.wikimedia_downloader import WikimediaDownloader


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
    ) -> None:
        self.registry = registry or SourceRegistry()
        self.snapshots = snapshots or SourceSnapshotStore()
        self.cache = cache or SourceCache()
        self.keycloak_fetcher = keycloak_fetcher or KeycloakDocsFetcher(snapshot_store=self.snapshots)
        self.wikimedia_downloader = wikimedia_downloader or WikimediaDownloader(snapshot_store=self.snapshots)

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
        descriptor = self.registry.get_source(source_id)
        if descriptor is None:
            raise ValueError("source_not_found")
        if not bool(descriptor.get("enabled", True)):
            return {"source_id": source_id, "status": "skipped", "reason_code": "source_disabled"}
        if dry_run:
            return {"source_id": source_id, "status": "planned", "reason_code": "dry_run"}
        source_type = str(descriptor.get("source_type") or "")
        descriptor_hash = str((descriptor.get("extensions") or {}).get("descriptor_hash") or "0" * 64)
        if source_type == "keycloak_docs":
            report = self.keycloak_fetcher.fetch(descriptor=descriptor, dry_run=False)
            pages = list(report.get("pages") or [])
            for page in pages:
                self.cache.put_raw(source_id=source_id, payload=str(page.get("raw_html") or ""))
                self.cache.put_extracted(source_id=source_id, payload=str(page.get("extracted_text") or ""))
            self.snapshots.mark_superseded(source_id=source_id, keep_snapshot_id=str(report["snapshot"]["snapshot_id"]))
            return {"source_id": source_id, "status": "ok", "report": report}
        if source_type == "wikimedia_dump":
            if not corpus_url or not destination_name:
                return {
                    "source_id": source_id,
                    "status": "queued",
                    "reason_code": "download_parameters_required",
                    "human_message": "Provide corpus_url and destination_name for dump refresh",
                }
            destination = Path("data/wiki_corpora") / str(destination_name)
            report = self.wikimedia_downloader.download(
                source_id=source_id,
                descriptor_hash=descriptor_hash,
                url=str(corpus_url),
                destination=destination,
                max_parallel=1,
            )
            self.cache.put_raw(source_id=source_id, payload=f"url={corpus_url}")
            self.cache.put_extracted(source_id=source_id, payload=f"destination={destination}")
            self.snapshots.mark_superseded(source_id=source_id, keep_snapshot_id=str(report["snapshot"]["snapshot_id"]))
            return {"source_id": source_id, "status": "ok", "report": report}
        return {"source_id": source_id, "status": "failed", "reason_code": "unsupported_source_type"}

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

