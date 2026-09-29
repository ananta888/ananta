from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import agent.sources.source_registry as source_registry_module
from agent.sources.source_registry import SourceRegistry


def _descriptor(source_id: str = "valid-source") -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": source_id,
        "source_type": "web_doc",
        "display_name": "Valid source",
        "enabled": True,
        "trust_level": "official",
        "fetch_source": {
            "url": "https://example.test/docs",
            "method": "GET",
            "refresh_interval": "24h",
            "cache_policy": "etag",
            "expected_format": "html",
        },
        "citation_source": {
            "canonical_url": "https://example.test/docs",
            "title": "Valid source",
            "publisher": "Example",
            "version_label": "current",
            "retrieved_at": "2026-07-30T00:00:00Z",
            "license_ref": "https://example.test/license",
            "citation_text": "Example documentation.",
        },
        "license": {
            "name": "Example",
            "ref": "https://example.test/license",
        },
        "snapshot_policy": {
            "immutable": True,
            "dedupe_by_hash": True,
        },
        "retention_policy": {
            "keep_latest": 2,
        },
        "extensions": {},
    }


def _multiprocess_create(root: str, start, results) -> None:
    start.wait()
    try:
        SourceRegistry(root=Path(root)).create_source(_descriptor())
    except ValueError as exc:
        results.put(str(exc))
    else:
        results.put("created")


def _crash_while_holding_registry_lock(root: str, acquired) -> None:
    registry = SourceRegistry(root=Path(root))
    with registry._registry_file_lock(create_storage=True):
        acquired.set()
        os._exit(23)


def test_registry_reads_do_not_create_storage(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    registry = SourceRegistry(root=root)

    assert not root.exists()
    assert registry.get_source("valid-source") is None
    assert registry.list_sources() == []
    assert registry.get_source_pack("valid-pack") is None
    assert not root.exists()


@pytest.mark.parametrize(
    "source_id",
    [
        "../../escape",
        "/absolute/path",
        "valid\u2215source",
        "valid\u2044source",
        "source\u0000id",
        "SOURCE-ID",
    ],
)
def test_registry_rejects_traversal_and_unicode_ids_before_path_resolution(
    tmp_path: Path,
    source_id: str,
) -> None:
    registry = SourceRegistry(root=tmp_path / "registry")

    assert registry.get_source(source_id) is None
    assert registry.get_source_pack(source_id) is None
    with pytest.raises(ValueError, match="source_id_invalid"):
        registry.create_source(_descriptor(source_id))
    assert not (tmp_path / "escape.json").exists()


def test_registry_rejects_descriptor_paths_outside_approved_roots(
    tmp_path: Path,
) -> None:
    registry = SourceRegistry(root=tmp_path / "registry")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_descriptor()), encoding="utf-8")

    with pytest.raises(ValueError, match="registry_path_outside_root"):
        registry._resolve_descriptor_path(str(outside))


def test_registry_rejects_symlinked_source_without_following_it(
    tmp_path: Path,
) -> None:
    registry = SourceRegistry(root=tmp_path / "registry")
    original = registry.create_source(_descriptor())
    target = registry.source_dir / "valid-source.json"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(original), encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    assert registry.get_source("valid-source") is None
    with pytest.raises(ValueError, match="registry_symlink_not_allowed"):
        registry.update_source(
            source_id="valid-source",
            descriptor={**original, "display_name": "Changed"},
        )
    assert json.loads(outside.read_text(encoding="utf-8")) == original


def test_registry_serializes_parallel_create_across_instances(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registry"
    registries = (SourceRegistry(root=root), SourceRegistry(root=root))

    def create(registry: SourceRegistry) -> str:
        try:
            registry.create_source(_descriptor())
        except ValueError as exc:
            return str(exc)
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, registries))

    assert sorted(outcomes) == ["created", "source_id_already_exists"]
    assert registries[0]._lock is registries[1]._lock


def test_registry_atomic_replace_preserves_previous_document_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SourceRegistry(root=tmp_path / "registry")
    original = registry.create_source(_descriptor())
    target = registry.source_dir / "valid-source.json"
    original_bytes = target.read_bytes()

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("simulated replace crash")

    monkeypatch.setattr(source_registry_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace crash"):
        registry.update_source(
            source_id="valid-source",
            descriptor={**original, "display_name": "Changed"},
        )

    assert target.read_bytes() == original_bytes
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_registry_serializes_create_across_processes(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_multiprocess_create,
            args=(str(tmp_path / "registry"), start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)

    assert all(not process.is_alive() for process in processes)
    assert sorted(results.get(timeout=1) for _ in processes) == [
        "created",
        "source_id_already_exists",
    ]


def test_registry_lock_is_released_after_process_crash(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("fork")
    acquired = context.Event()
    process = context.Process(
        target=_crash_while_holding_registry_lock,
        args=(str(tmp_path / "registry"), acquired),
    )
    process.start()
    assert acquired.wait(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 23

    created = SourceRegistry(
        root=tmp_path / "registry"
    ).create_source(_descriptor())
    assert created["source_id"] == "valid-source"


def test_registry_rejects_symlink_lockfile(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    lock_parent = root / "sources"
    lock_parent.mkdir(parents=True)
    outside = tmp_path / "outside.lock"
    outside.write_text("", encoding="utf-8")
    (lock_parent / ".registry.lock").symlink_to(outside)

    with pytest.raises(ValueError, match="lock_symlink"):
        SourceRegistry(root=root).create_source(_descriptor())
