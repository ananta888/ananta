from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_semantic_media_speech_baseline import OUTPUT, PROBES, build_report


def test_tracked_baseline_is_deterministic_and_source_valid() -> None:
    expected = json.dumps(build_report(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    assert OUTPUT.read_text(encoding="utf-8") == expected
    assert json.dumps(build_report(), sort_keys=True) == json.dumps(build_report(), sort_keys=True)


def test_baseline_has_required_gap_categories_without_invented_source_ids() -> None:
    report = build_report()
    findings = {row["finding"]: row for row in report["findings"]}
    required = {
        "permission_key_drift",
        "pair_payload_e2ee",
        "voice_chunk_reassembly_scope",
        "hub_relay_durability",
        "browser_media_tracks",
        "sfu_control_plane",
        "voice_alignment",
        "voice_consent_categories",
        "speech_training_boundary",
    }
    assert required <= set(findings)
    assert not any("source_id" in row or "SRC_" in json.dumps(row) for row in report["findings"])
    assert all(not Path(row["path"]).is_absolute() for row in report["findings"])
    assert all(probe.classification in report["classifications"] for probe in PROBES)
