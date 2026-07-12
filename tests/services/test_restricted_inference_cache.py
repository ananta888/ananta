from __future__ import annotations

from dataclasses import replace

from agent.services.restricted_inference_cache import (
    RestrictedInferenceCache,
    RestrictedInferenceCacheKey,
)


def _key(tenant: str, input_hash_seed: str) -> RestrictedInferenceCacheKey:
    return RestrictedInferenceCacheKey.build(
        tenant_id=tenant,
        operation="classify",
        manifest_digest="a" * 64,
        policy_hash="policy-1",
        config={"revision": "immutable"},
        payload={"hash_seed": input_hash_seed},
    )


def test_cache_ttl_and_lru_eviction_are_deterministic() -> None:
    now = [1_000_000_000]
    cache = RestrictedInferenceCache(max_entries=2, ttl_seconds=1, monotonic_ns=lambda: now[0])
    first = _key("tenant-a", "first")
    second = _key("tenant-a", "second")
    third = _key("tenant-a", "third")
    cache.put(first, {"label": "a"})
    cache.put(second, {"label": "b"})
    assert cache.get(first) == {"label": "a"}
    cache.put(third, {"label": "c"})

    assert cache.get(second) is None
    assert cache.get(first) == {"label": "a"}
    now[0] += 1_000_000_001
    assert cache.get(first) is None


def test_cache_keys_cannot_cross_tenant_policy_or_revision() -> None:
    base = _key("tenant-a", "same")

    assert base.digest() != replace(base, tenant_id="tenant-b").digest()
    assert base.digest() != replace(base, policy_hash="policy-2").digest()
    assert base.digest() != replace(base, manifest_digest="b" * 64).digest()


def test_corrupted_entry_is_discarded_without_returning_payload() -> None:
    cache = RestrictedInferenceCache(max_entries=1, ttl_seconds=60)
    key = _key("tenant-a", "same")
    cache.put(key, {"label": "safe"})
    digest = key.digest()
    entry = cache._entries[digest]  # type: ignore[attr-defined]
    cache._entries[digest] = replace(entry, payload=b"tampered")  # type: ignore[attr-defined]

    assert cache.get(key) is None
    assert len(cache) == 0
