from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceGroupPreview,
    group_preview_digest,
    speech_evidence_quality_policy_digest,
    speech_evidence_speaker_scope_digest,
)
from ananta_contracts.speech_evidence_sync import (
    group_preview_group_id,
    group_preview_resolution_digest,
)
from tests.speech_evidence_sync_support import message

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "schemas" / "voice"


def _validator(name: str) -> Draft202012Validator:
    common = json.loads((SCHEMA_ROOT / "speech_evidence_common.v1.json").read_text())
    schema = json.loads((SCHEMA_ROOT / name).read_text())
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(schema, registry=registry)


def test_all_closed_wire_schema_families_accept_matching_signed_messages() -> None:
    cases = {
        "speech_evidence_inventory.v1.json": "inventory",
        "speech_evidence_diff.v1.json": "diff",
        "speech_evidence_offer.v2.json": "offer",
        "speech_evidence_chunk.v1.json": "chunk",
        "speech_evidence_resolution.v1.json": "resolution",
        "speech_evidence_receipt.v1.json": "receipt",
        "speech_evidence_revocation.v1.json": "revocation",
    }
    for schema, message_type in cases.items():
        _validator(schema).validate(message(message_type))


def test_chunk_and_revocation_schemas_include_acknowledgements() -> None:
    _validator("speech_evidence_chunk.v1.json").validate(message("chunk_ack"))
    _validator("speech_evidence_revocation.v1.json").validate(message("revocation_ack"))


def test_legacy_offer_without_signed_preview_is_explicitly_rejected() -> None:
    legacy = message("offer")
    legacy["protocol_version"] = "ananta.speech-evidence-sync.v1"
    legacy["payload"].pop("group_previews")
    assert list(_validator("speech_evidence_offer.v1.json").iter_errors(legacy))


def test_schemas_reject_unknown_security_fields_and_oversized_chunk() -> None:
    unknown = message("inventory")
    unknown["raw_key"] = "forbidden"
    assert list(_validator("speech_evidence_inventory.v1.json").iter_errors(unknown))

    oversized = message("chunk")
    oversized["payload"]["plaintext_bytes"] = 65_537
    assert list(_validator("speech_evidence_chunk.v1.json").iter_errors(oversized))


def test_shared_python_typescript_fixture_declares_required_negative_cases() -> None:
    fixture = json.loads(
        (ROOT / "frontend-angular/src/app/services/fixtures/speech-evidence-protocol.v1.json").read_text()
    )
    assert {row["name"] for row in fixture["cases"]} == {
        "valid",
        "stale",
        "tampered",
        "replayed",
        "wrong-peer",
        "oversized",
        "revoked",
    }
    preview_fixture = json.loads(
        (ROOT / "frontend-angular/src/app/services/fixtures/speech-evidence-offer-preview.v2.json").read_text()
    )
    assert {row["name"] for row in preview_fixture["negative_cases"]} == {
        "missing",
        "forged",
        "stale",
        "wrong-source-group",
        "resolution-digest-mismatch",
        "scope-expansion",
    }
    forbidden = {"audio", "transcript", "text", "features", "embedding", "local_path"}
    assert not forbidden & set(preview_fixture["valid"])


def test_shared_offer_preview_fixture_matches_python_derived_bindings() -> None:
    fixture = json.loads(
        (ROOT / "frontend-angular/src/app/services/fixtures/speech-evidence-offer-preview.v2.json").read_text()
    )
    preview = fixture["valid"]
    source_digest = preview["source_group_digest"]
    revision = preview["revision"]

    assert preview["group_id"] == group_preview_group_id(source_digest, revision)
    assert preview["resolution_digest"] == group_preview_resolution_digest(source_digest, revision)
    assert preview["speaker_scope_digest"] == speech_evidence_speaker_scope_digest(
        pair_id="pair-test",
        epoch=7,
        speaker_id="peer-a",
    )
    assert preview["quality_digest"] == speech_evidence_quality_policy_digest()
    assert fixture["preview_set_digest"] == group_preview_digest(
        (SpeechEvidenceGroupPreview.from_mapping(preview),)
    )
