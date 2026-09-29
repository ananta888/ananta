from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.source_snapshot_store import SourceSnapshotStore


class _Response:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")
        self.headers = {"ETag": "e1", "Last-Modified": "yesterday", "Cache-Control": "max-age=300"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_keycloak_fetcher_fetches_pages_and_creates_snapshot(monkeypatch, tmp_path: Path) -> None:
    def _fake_urlopen(request: Any, timeout: int = 0) -> _Response:
        _ = request
        _ = timeout
        return _Response("<html><body><h1>Keycloak</h1>Docs</body></html>")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    store = SourceSnapshotStore(root=tmp_path)
    fetcher = KeycloakDocsFetcher(snapshot_store=store)
    descriptor = {
        "source_id": "keycloak-official-docs",
        "fetch_source": {
            "url": "https://www.keycloak.org/documentation",
            "additional_urls": ["https://www.keycloak.org/guides"],
        },
        "extensions": {"descriptor_hash": "a" * 64},
    }
    report = fetcher.fetch(descriptor=descriptor, dry_run=False)
    assert report["source_id"] == "keycloak-official-docs"
    assert len(report["pages"]) == 2
    assert report["snapshot"]["status"] == "indexed"
    assert store.latest_indexed_snapshot(source_id="keycloak-official-docs") is not None


def test_keycloak_fetcher_dry_run_does_not_persist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response("<html>ok</html>"))
    store = SourceSnapshotStore(root=tmp_path)
    fetcher = KeycloakDocsFetcher(snapshot_store=store)
    report = fetcher.fetch(
        descriptor={"source_id": "keycloak-official-docs", "fetch_source": {"url": "https://www.keycloak.org/documentation"}},
        dry_run=True,
    )
    assert report["snapshot"]["status"] == "validating"
    assert store.latest_indexed_snapshot(source_id="keycloak-official-docs") is None

