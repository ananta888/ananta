from __future__ import annotations

import hashlib
import urllib.request
from datetime import UTC, datetime
from typing import Any

from agent.sources.source_snapshot_store import SourceSnapshotStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _extract_text_from_html(html: str) -> str:
    text = str(html or "")
    return " ".join(text.replace("<", " <").replace(">", "> ").split())


class KeycloakDocsFetcher:
    def __init__(self, *, timeout_seconds: int = 20, snapshot_store: SourceSnapshotStore | None = None) -> None:
        self.timeout_seconds = int(timeout_seconds)
        self.snapshot_store = snapshot_store or SourceSnapshotStore()

    def fetch(
        self,
        *,
        descriptor: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        fetch_source = dict(descriptor.get("fetch_source") or {})
        source_id = str(descriptor.get("source_id") or "keycloak-official-docs")
        primary_url = str(fetch_source.get("url") or "").strip()
        additional = [str(item).strip() for item in list(fetch_source.get("additional_urls") or []) if str(item).strip()]
        urls = [primary_url, *additional]
        pages: list[dict[str, Any]] = []
        for url in urls:
            request = urllib.request.Request(url, headers={"User-Agent": "ananta-source-fetcher/1.0"})
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = response.read().decode("utf-8", errors="replace")
                headers = dict(response.headers.items())
            pages.append(
                {
                    "url": url,
                    "status_code": 200,
                    "headers": {
                        "etag": str(headers.get("ETag") or ""),
                        "last_modified": str(headers.get("Last-Modified") or ""),
                        "cache_control": str(headers.get("Cache-Control") or ""),
                    },
                    "raw_html": payload,
                    "extracted_text": _extract_text_from_html(payload),
                }
            )
        descriptor_hash = str((descriptor.get("extensions") or {}).get("descriptor_hash") or hashlib.sha256(repr(descriptor).encode("utf-8")).hexdigest())
        snapshot = self.snapshot_store.build_snapshot(
            source_id=source_id,
            descriptor_hash=descriptor_hash,
            content_payload=pages,
            metadata_payload={"url_count": len(urls), "retrieved_at": _now_iso()},
            status="indexed" if not dry_run else "validating",
            retrieved_at=_now_iso(),
        )
        if not dry_run:
            self.snapshot_store.save_snapshot(snapshot)
        return {"source_id": source_id, "snapshot": snapshot, "pages": pages, "dry_run": bool(dry_run)}

