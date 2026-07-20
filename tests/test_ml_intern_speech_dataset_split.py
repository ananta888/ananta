from __future__ import annotations

from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.ml_intern_speech_dataset_split_service import MlInternSpeechDatasetSplitService
from agent.services.speech_dataset_privacy_preview_service import SpeechDatasetPrivacyPreviewService
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    digest,
    manifest_record,
    principal,
)


def _manifest(prefix: str):
    records = [manifest_record(f"{prefix}-{index}", group_suffix=str(index)) for index in range(6)]
    # Peer copy / transformed audio: distinct contributors and sources, but the
    # same near-duplicate group must remain together.
    records[1]["near_duplicate_group_id"] = records[0]["near_duplicate_group_id"]
    return MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(), consent_authority=AllowDatasetConsent()
    ).build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=records,
        curation_report_digest=digest(f"report-{prefix}"),
    )[0]


def test_split_is_reproducible_nonempty_and_has_no_group_leakage() -> None:
    prefix = "speech-split"
    manifest = _manifest(prefix)
    service = MlInternSpeechDatasetSplitService()
    first = service.split(principal(prefix), manifest, validation_ratio=0.3, seed=42)
    second = service.split(principal(prefix), manifest, validation_ratio=0.3, seed=42)
    assert first == second
    assert first.train_count > 0 and first.validation_count > 0
    assert (
        first.assignments[manifest["records"][0]["record_digest"]]
        == first.assignments[manifest["records"][1]["record_digest"]]
    )


def test_privacy_preview_is_aggregate_only_and_raw_audio_is_default_denied() -> None:
    prefix = "speech-preview"
    manifest = _manifest(prefix)
    preview = SpeechDatasetPrivacyPreviewService().preview(
        principal(prefix), manifest, admission_findings={"one": {"decision": "quarantined", "reason_codes": ["pii"]}}
    )
    assert preview["record_count"] == 6
    assert preview["raw_audio_preview"] == {"authorized": False, "refs": []}
    assert "source_digest" not in preview
    import pytest

    with pytest.raises(Exception, match="speech_preview_raw_audio_grant_missing"):
        SpeechDatasetPrivacyPreviewService().preview(
            principal(prefix), manifest, include_raw_audio=True, raw_audio_preview_grant_ref="peer-claim"
        )


def test_leakage_fixtures_cover_phrase_reconnect_peer_copy_transform_and_revisions() -> None:
    prefix = "speech-split-leakage-matrix"
    records = [manifest_record(f"{prefix}-{index}", group_suffix=str(index)) for index in range(10)]

    # Same phrase is represented content-free by the near-duplicate detector's
    # group. Contributors and source digests intentionally differ.
    records[1]["near_duplicate_group_id"] = records[0]["near_duplicate_group_id"]
    # A reconnect retains the logical session group even though capture and
    # source identities rotate.
    records[3]["session_group_id"] = records[2]["session_group_id"]
    # Peer copy and transformed audio share the detector group, not a global
    # transcript identifier.
    records[5]["near_duplicate_group_id"] = records[4]["near_duplicate_group_id"]
    records[6]["near_duplicate_group_id"] = records[4]["near_duplicate_group_id"]
    # Multiple transcript corrections of one source stay in the same source,
    # session and utterance component while retaining immutable record IDs.
    records[8]["source_digest"] = records[7]["source_digest"]
    records[8]["session_group_id"] = records[7]["session_group_id"]
    records[8]["utterance_family_id"] = records[7]["utterance_family_id"]
    for field in records[8]["field_provenance"].values():
        field["source_digest"] = records[7]["source_digest"]

    manifest = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(), consent_authority=AllowDatasetConsent()
    ).build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=records,
        curation_report_digest=digest(f"report-{prefix}"),
    )[0]
    split = MlInternSpeechDatasetSplitService().split(
        principal(prefix), manifest, validation_ratio=0.3, seed=20260719
    )

    for indexes in ((0, 1), (2, 3), (4, 5, 6), (7, 8)):
        partitions = {
            split.assignments[str(records[index]["record_digest"])] for index in indexes
        }
        assert len(partitions) == 1
    assert split.train_count > 0 and split.validation_count > 0
