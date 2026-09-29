from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.services.voice_deletion_ledger import VoiceDeletionLedger
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal


def test_same_client_idempotency_key_is_isolated_by_deletion_scope(tmp_path) -> None:
    ledger = VoiceDeletionLedger(tmp_path / "ledger.jsonl")
    tombstones = VoiceDeletionTombstoneRepository(ledger=ledger)
    first = VoicePrincipal(tenant_id="tenant-alpha", subject="owner")
    second = VoicePrincipal(tenant_id="tenant-beta", subject="owner")

    first_claim = tombstones.claim(first, "profile", idempotency_key="same-client-key")
    second_claim = tombstones.claim(second, "profile", idempotency_key="same-client-key")
    first_replay = tombstones.claim(first, "profile", idempotency_key="same-client-key")

    assert first_claim.replayed is False
    assert second_claim.replayed is False
    assert first_replay.replayed is True
    assert first_claim.scope_digest != second_claim.scope_digest
    assert len(ledger.read_all()) == 2
    persisted = ledger.path.read_text(encoding="utf-8")
    assert "same-client-key" not in persisted
    assert "tenant-alpha" not in persisted
    assert "tenant-beta" not in persisted
    assert "owner" not in persisted


def test_segmented_ledger_is_bounded_replays_from_cache_and_fails_closed_at_capacity(tmp_path) -> None:
    ledger = VoiceDeletionLedger(
        tmp_path / "bounded-ledger.jsonl",
        max_records_per_segment=2,
        max_total_records=3,
    )
    tombstones = VoiceDeletionTombstoneRepository(ledger=ledger)
    first = VoicePrincipal(tenant_id="bounded-tenant-a", subject="owner")
    second = VoicePrincipal(tenant_id="bounded-tenant-b", subject="owner")
    third = VoicePrincipal(tenant_id="bounded-tenant-c", subject="owner")

    first_claim = tombstones.claim(first, "profile", idempotency_key="bounded-key-a")
    with patch.object(ledger, "_load_records_unlocked", wraps=ledger._load_records_unlocked) as reload_records:
        tombstones.claim(second, "profile", idempotency_key="bounded-key-b")
        assert reload_records.call_count == 0
    tombstones.claim(third, "profile", idempotency_key="bounded-key-c")

    assert len(ledger.segment_paths) == 1
    assert len(ledger.segment_paths[0].read_text(encoding="utf-8").splitlines()) == 2
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 1
    assert len(ledger.read_all()) == 3
    assert tombstones.claim(first, "profile", idempotency_key="bounded-key-a").replayed is True
    assert first_claim.deleted_at == ledger.read_all()[0].deleted_at

    with pytest.raises(VoiceGovernanceError) as exhausted:
        tombstones.claim(
            VoicePrincipal(tenant_id="bounded-tenant-d", subject="owner"),
            "profile",
            idempotency_key="bounded-key-d",
        )
    assert exhausted.value.code == "voice_deletion_ledger.capacity_exhausted"

    persisted = "".join(
        path.read_text(encoding="utf-8") for path in (*ledger.segment_paths, ledger.path)
    )
    for raw_value in ("bounded-tenant-a", "bounded-tenant-b", "bounded-tenant-c", "bounded-key"):
        assert raw_value not in persisted
