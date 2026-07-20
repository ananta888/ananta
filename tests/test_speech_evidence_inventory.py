from __future__ import annotations

import time
import tracemalloc
from dataclasses import replace

from tests.speech_evidence_sync_support import digest
from voice_runtime.evidence_inventory import (
    EvidenceConsentScope,
    EvidenceInventoryBuilder,
    EvidenceLeaf,
)
from voice_runtime.evidence_merkle import diff_inventories


def _scope(**changes) -> EvidenceConsentScope:
    values = {
        "pair_id": "pair-test",
        "direction": "sender_to_receiver",
        "purpose": "speech_dataset_curation",
        "data_classes": frozenset({"text_corrections", "vocabulary"}),
        "consent_version": 3,
        "retention_until_ms": 3_000_000,
        "epoch": 7,
    }
    values.update(changes)
    return EvidenceConsentScope(**values)


def _leaf(index: int, **changes) -> EvidenceLeaf:
    values = {
        "group_id": f"private-human-name-{index}",
        "pair_id": "pair-test",
        "direction": "sender_to_receiver",
        "purpose": "speech_dataset_curation",
        "data_class": "text_corrections",
        "payload_digest": digest(f"payload-{index}"),
        "size_bytes": 16,
        "consent_version": 3,
        "retention_until_ms": 2_500_000,
        "epoch": 7,
        "revoked": False,
    }
    values.update(changes)
    return EvidenceLeaf(**values)


def test_inventory_filters_scope_and_uses_opaque_pair_keyed_ids() -> None:
    builder = EvidenceInventoryBuilder(pair_key=b"p" * 32)
    inventory = builder.build(
        (
            _leaf(1),
            _leaf(2, pair_id="foreign-pair"),
            _leaf(3, data_class="raw_audio"),
            _leaf(4, revoked=True),
            _leaf(5, retention_until_ms=999_999),
        ),
        scope=_scope(),
        now_ms=1_000_000,
    )
    assert inventory.leaf_count == 1
    assert inventory.total_bytes == 16
    assert all("private-human-name" not in group_id for group_id in inventory.leaves)


def test_merkle_root_and_diff_are_deterministic_and_payload_free() -> None:
    builder = EvidenceInventoryBuilder(pair_key=b"p" * 32)
    one = builder.build((_leaf(2), _leaf(1)), scope=_scope(), now_ms=1_000_000)
    two = builder.build((_leaf(1), _leaf(2)), scope=_scope(), now_ms=1_000_000)
    assert one.root_digest == two.root_digest
    target = builder.build(
        (_leaf(1), replace(_leaf(2), payload_digest=digest("changed")), _leaf(3)),
        scope=_scope(),
        now_ms=1_000_000,
    )
    diff = diff_inventories(
        one.leaves,
        target.leaves,
        pair_key=b"p" * 32,
        scope_digest=target.scope_digest,
        consent_version=target.consent_version,
        epoch=target.epoch,
    )
    assert len(diff.missing_group_ids) == 1
    assert len(diff.changed_group_ids) == 1
    assert all("payload" not in value for value in (*diff.missing_group_ids, *diff.changed_group_ids))


def test_consent_revoke_ttl_and_epoch_invalidate_root_and_cursor_without_removed_names() -> None:
    builder = EvidenceInventoryBuilder(pair_key=b"p" * 32)
    active = builder.build((_leaf(1),), scope=_scope(), now_ms=1_000_000)
    revoked = builder.build((_leaf(1, revoked=True),), scope=_scope(), now_ms=1_000_000)
    changed_consent = builder.build(
        (_leaf(1, consent_version=4, epoch=8),),
        scope=_scope(consent_version=4, epoch=8),
        now_ms=1_000_000,
    )
    expired = builder.build((_leaf(1),), scope=_scope(), now_ms=2_500_000)
    assert len({active.root_digest, revoked.root_digest, changed_consent.root_digest}) == 3
    assert revoked.root_digest == expired.root_digest
    assert active.cursor_digest != changed_consent.cursor_digest
    removal_diff = diff_inventories(
        active.leaves,
        revoked.leaves,
        pair_key=b"p" * 32,
        scope_digest=revoked.scope_digest,
        consent_version=revoked.consent_version,
        epoch=revoked.epoch,
    )
    assert not removal_diff.missing_group_ids and not removal_diff.changed_group_ids


def test_hundred_thousand_leaf_inventory_is_streamed_with_bounded_memory() -> None:
    builder = EvidenceInventoryBuilder(pair_key=b"p" * 32)
    started = time.perf_counter()
    tracemalloc.start()
    inventory = builder.build((_leaf(index) for index in range(100_000)), scope=_scope(), now_ms=1_000_000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert inventory.leaf_count == 100_000
    assert time.perf_counter() - started < 15.0
    assert peak < 128 * 1024 * 1024
