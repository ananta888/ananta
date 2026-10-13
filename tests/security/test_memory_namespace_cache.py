"""Synthetic cache permissions; no production evidence or network dependencies."""

import json
from dataclasses import replace

from agent.adapters.namespace_memory_cache import NamespaceMemoryCache
from agent.services.memory_storage_boundary import MemoryStorageBoundary
from agent.services.pre_model_context_cache import ContextPackageCache
from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from tests.security.memory_fixtures import SyntheticWorkerMemoryAuthorization, synthetic_memory_access
from worker.retrieval.cache import RetrievalCache


def cache(root):
    return NamespaceMemoryCache(root, boundary=MemoryStorageBoundary("hub-context-cache"))


def access(**kwargs):
    return synthetic_memory_access(channels=("hub-context-cache",), scopes=(MemoryScope.run,), **kwargs)


def test_absent_authority_has_no_io_and_legacy_files_are_ignored(tmp_path):
    root = tmp_path / "new"
    storage = cache(root)
    assert storage.get("key") is None
    assert not storage.put("key", {"secret": "value"})
    assert not root.exists()
    root.mkdir()
    legacy = root / "legacy.json"
    legacy.write_text(json.dumps({"payload": {"secret": "old"}}))
    with access():
        assert storage.get("legacy") is None
        assert storage.count() == 0
        assert storage.invalidate() == 0
    assert legacy.exists()


def test_roundtrip_detached_manifest_and_policy_binding(tmp_path):
    storage = cache(tmp_path)
    with access() as (policies, _):
        payload = {"values": ["one"]}
        assert storage.put("key", payload, manifest_hash="revision")
        payload["values"].append("two")
        result = storage.get("key", manifest_hash="revision")
        assert result == {"values": ["one"]}
        result["values"].append("changed")
        assert storage.get("key") == {"values": ["one"]}
        assert storage.get("key", manifest_hash="different") is None
        policies.value = replace(policies.value, policy_revision="changed-policy")
        assert storage.get("key") is None


def test_namespace_invalidation_cannot_delete_another_task(tmp_path):
    storage = cache(tmp_path)
    with access(task_id="first"):
        assert storage.put("key", {"owner": "first"})
        with access(task_id="second"):
            assert storage.get("key") is None
            assert storage.put("key", {"owner": "second"})
            assert storage.invalidate() == 1
        assert storage.get("key") == {"owner": "first"}
        assert storage.count() == 1


def test_update_needs_separate_permission(tmp_path):
    storage = cache(tmp_path)
    with access(operations=(MemoryOperation.read, MemoryOperation.append)):
        assert storage.put("key", {"version": 1})
        assert not storage.put("key", {"version": 2})
        assert storage.get("key") == {"version": 1}


def test_corrupt_or_symlink_cache_is_never_returned(tmp_path):
    storage = cache(tmp_path / "cache")
    with access():
        assert storage.put("key", {"data": "valid"})
        file = next(storage.root.rglob("*.json"))
        file.write_text("[]")
        assert storage.get("key") is None
        file.unlink()
        canary = tmp_path / "canary"
        canary.write_text("private outside cache")
        file.symlink_to(canary)
        assert storage.get("key") is None
        assert canary.read_text() == "private outside cache"


def test_sensitive_toggle_is_not_a_permission(tmp_path):
    storage = ContextPackageCache(cache_dir=tmp_path)
    with access():
        assert not storage.put("secret", {"has_sensitive_content": True}, allow_sensitive=True)
        assert storage.put("public", {"has_sensitive_content": False})
        assert storage.get("public") == {"has_sensitive_content": False}


def test_worker_cache_requires_live_hub_authority_and_owns_snapshot():
    closed = RetrievalCache()
    closed.put(key="same", payload={"data": "denied"}, quality_score=1)
    assert closed.get(key="same") is None
    authority = SyntheticWorkerMemoryAuthorization()
    storage = RetrievalCache(authorization=authority)
    value = {"items": ["one"]}
    storage.put(key="same", payload=value, quality_score=1)
    value["items"].append("two")
    assert storage.get(key="same") == {"items": ["one"]}
    authority.namespace = replace(authority.namespace, task_id="other")
    assert storage.get(key="same") is None
    assert storage.stats() == {"entry_count": 0}
    authority.namespace = replace(authority.namespace, task_id="t1")
    authority.digest = "b" * 64
    assert storage.get(key="same") is None
    authority.digest = "a" * 64
    authority.operations.remove(MemoryOperation.read)
    assert storage.get(key="same") is None
