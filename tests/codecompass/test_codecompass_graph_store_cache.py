from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.services import codecompass_graph_store_cache as cache_module


def _install_store_factory(monkeypatch: pytest.MonkeyPatch) -> Mock:
    factory = Mock(side_effect=lambda **_kwargs: object())
    monkeypatch.setattr(cache_module, "CodeCompassGraphStore", factory)
    return factory


def _write_artifact(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _replace_at_new_mtime(path: Path, content: str) -> None:
    before = path.stat()
    assert len(content.encode("utf-8")) == before.st_size
    path.write_text(content, encoding="utf-8")
    os.utime(
        path,
        ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
    )
    assert path.stat().st_mtime_ns != before.st_mtime_ns


def test_get_reuses_store_while_artifact_signatures_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "graph.json"
    metrics_path = tmp_path / "metrics.json"
    _write_artifact(index_path, "graph-v1")
    _write_artifact(metrics_path, "metric-v1")
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(maximum_entries=2)

    first = cache.get(index_path=index_path, visual_metrics_path=metrics_path)
    second = cache.get(index_path=index_path, visual_metrics_path=metrics_path)

    assert second is first
    store_factory.assert_called_once_with(
        index_path=index_path,
        max_artifact_bytes=cache_module.MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
        visual_metrics_path=metrics_path,
    )


@pytest.mark.parametrize("changed_artifact", ["index", "metrics"])
def test_get_invalidates_store_when_an_artifact_signature_changes(
    changed_artifact: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "graph.json"
    metrics_path = tmp_path / "metrics.json"
    _write_artifact(index_path, "graph-v1")
    _write_artifact(metrics_path, "metric-v1")
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(maximum_entries=2)
    first = cache.get(index_path=index_path, visual_metrics_path=metrics_path)

    if changed_artifact == "index":
        _replace_at_new_mtime(index_path, "graph-v2")
    else:
        _replace_at_new_mtime(metrics_path, "metric-v2")
    second = cache.get(index_path=index_path, visual_metrics_path=metrics_path)

    assert second is not first
    assert store_factory.call_count == 2


def test_get_evicts_the_least_recently_used_store_at_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / name for name in ("a.json", "b.json", "c.json")]
    for path in paths:
        _write_artifact(path, path.stem)
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(maximum_entries=2)

    store_a = cache.get(index_path=paths[0], visual_metrics_path=None)
    store_b = cache.get(index_path=paths[1], visual_metrics_path=None)
    assert cache.get(index_path=paths[0], visual_metrics_path=None) is store_a

    store_c = cache.get(index_path=paths[2], visual_metrics_path=None)
    assert cache.get(index_path=paths[0], visual_metrics_path=None) is store_a
    reloaded_b = cache.get(index_path=paths[1], visual_metrics_path=None)

    assert store_c is not store_a
    assert reloaded_b is not store_b
    assert store_factory.call_count == 4


def test_clear_invalidates_all_cached_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "graph.json"
    _write_artifact(index_path, "graph-v1")
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(maximum_entries=2)
    first = cache.get(index_path=index_path, visual_metrics_path=None)

    cache.clear()
    second = cache.get(index_path=index_path, visual_metrics_path=None)

    assert second is not first
    assert store_factory.call_count == 2


def test_get_does_not_cache_an_entry_larger_than_source_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "graph.json"
    metrics_path = tmp_path / "metrics.json"
    _write_artifact(index_path, "graph")
    _write_artifact(metrics_path, "metrics")
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(
        maximum_entries=4,
        maximum_source_bytes=5,
    )

    first = cache.get(index_path=index_path, visual_metrics_path=metrics_path)
    second = cache.get(index_path=index_path, visual_metrics_path=metrics_path)

    assert second is not first
    assert store_factory.call_count == 2


def test_get_evicts_by_combined_source_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _write_artifact(first_path, "123456")
    _write_artifact(second_path, "abcdef")
    store_factory = _install_store_factory(monkeypatch)
    cache = cache_module.CodeCompassGraphStoreCache(
        maximum_entries=4,
        maximum_source_bytes=10,
    )

    first = cache.get(index_path=first_path, visual_metrics_path=None)
    second = cache.get(index_path=second_path, visual_metrics_path=None)
    assert cache.get(index_path=second_path, visual_metrics_path=None) is second
    reloaded_first = cache.get(
        index_path=first_path,
        visual_metrics_path=None,
    )

    assert reloaded_first is not first
    assert store_factory.call_count == 3


def test_cache_rejects_non_positive_source_byte_budget() -> None:
    with pytest.raises(ValueError, match="graph_store_cache_bytes_invalid"):
        cache_module.CodeCompassGraphStoreCache(maximum_source_bytes=0)
