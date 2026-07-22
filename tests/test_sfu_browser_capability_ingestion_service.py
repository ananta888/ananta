from __future__ import annotations

import json

import pytest

from agent.repositories.sfu_browser_capability_repository import InMemorySfuBrowserCapabilityRepository
from agent.services.sfu_browser_capability_ingestion_service import (
    SfuBrowserCapabilityCommand,
    SfuBrowserCapabilityError,
    SfuBrowserCapabilityIngestionService,
    SfuCapabilityAdmissionScope,
)


NOW = 1_800_000_000.0
SCOPE = SfuCapabilityAdmissionScope("tenant-a", "room-a", "user-a", 3, 7)


def _document(sequence: int = 1) -> bytes:
    return json.dumps({
        "schema": "ananta.browser-media-capability-observation.v1",
        "schema_version": 1,
        "capability_version": "coarse-v1",
        "tenant_ref": "tenant-a",
        "room_ref": "room-a",
        "admission_epoch": 3,
        "membership_epoch": 7,
        "browser_instance_pseudonym": "room-bip_AAAAAAAAAAAAAAAAAAAAAA",
        "sequence": sequence,
        "issued_at": "2027-01-15T08:00:00Z",
        "ttl_seconds": 300,
        "pseudonym_rotation_seconds": 900,
        "capability_bucket_combinations_max": 8,
        "report_bytes_max": 2048,
        "authorization_effect": "none",
        "capability_buckets": [{
            "codec_bucket": "video_vp8", "layering_bucket": "simulcast",
            "encoded_transform_bucket": "available", "decode_bucket": "video_baseline",
            "evidence_bucket": "static_capability_query",
        }],
    }, separators=(",", ":")).encode()


def test_persists_version_and_replays_identical_sequence() -> None:
    service = SfuBrowserCapabilityIngestionService(InMemorySfuBrowserCapabilityRepository(), clock=lambda: NOW)
    first = service.ingest(SfuBrowserCapabilityCommand(_document(), SCOPE, 0))
    replay = service.ingest(SfuBrowserCapabilityCommand(_document(), SCOPE, first.version))
    assert first == replay
    assert first.capability_class == "advanced"


def test_rejects_stale_sequence_after_new_service_instance() -> None:
    repository = InMemorySfuBrowserCapabilityRepository()
    first = SfuBrowserCapabilityIngestionService(repository, clock=lambda: NOW)
    saved = first.ingest(SfuBrowserCapabilityCommand(_document(2), SCOPE, 0))
    restarted = SfuBrowserCapabilityIngestionService(repository, clock=lambda: NOW)
    with pytest.raises(SfuBrowserCapabilityError, match="sfu_capability_replay"):
        restarted.ingest(SfuBrowserCapabilityCommand(_document(1), SCOPE, saved.version))


def test_cross_room_report_fails_closed() -> None:
    service = SfuBrowserCapabilityIngestionService(InMemorySfuBrowserCapabilityRepository(), clock=lambda: NOW)
    with pytest.raises(SfuBrowserCapabilityError, match="sfu_capability_cross_scope"):
        service.ingest(SfuBrowserCapabilityCommand(_document(), SfuCapabilityAdmissionScope("tenant-a", "other", "u", 3, 7), 0))
