from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_layer_profile_policy import (
    LayerProfileRequest,
    LayerSourceCapability,
    SfuBroadcastLayerProfileError,
    SfuBroadcastLayerProfilePolicy,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "sfu_broadcast_layer_profiles.json"


def capability(*, codecs=frozenset({"video_vp8", "video_vp9"}), modes=frozenset({"none", "simulcast"}), **overrides):
    values = {
        "known": True, "codec_classes": codecs, "scalability_classes": modes,
        "max_encodings": 3, "max_width_px": 1920, "max_height_px": 1080, "max_fps": 30,
        "max_aggregate_bitrate_bps": 4_000_000, "max_keyframe_interval_ms": 5000,
        "e2ee_compatible": True,
    }
    values.update(overrides)
    return LayerSourceCapability(**values)


def request(**overrides):
    source = capability()
    values = {
        "media_kind": "camera", "parent_publication": source, "browser": source,
        "sfu": source, "receiver": source,
    }
    values.update(overrides)
    return LayerProfileRequest(**values)


def test_effective_profile_is_the_bounded_intersection_of_every_authority():
    resolved = SfuBroadcastLayerProfilePolicy(PROFILE).resolve(request(
        browser=capability(codecs=frozenset({"video_vp8"}), max_width_px=1280, max_height_px=720),
        receiver=capability(modes=frozenset({"none"}), max_aggregate_bitrate_bps=900_000),
    ))
    assert resolved.safe_outcome == "apply_projection"
    assert resolved.limits is not None
    assert resolved.limits.codec_classes == ("video_vp8",)
    assert resolved.limits.scalability_classes == ("none",)
    assert resolved.limits.max_width_px == 1280
    assert resolved.limits.max_height_px == 720
    assert resolved.limits.max_aggregate_bitrate_bps == 900_000


def test_unknown_legacy_empty_and_e2ee_incompatible_inputs_fail_safe():
    policy = SfuBroadcastLayerProfilePolicy(PROFILE)
    assert policy.resolve(request(browser=LayerSourceCapability.unknown())).safe_outcome == "ordinary_fallback"
    assert policy.resolve(request(legacy_client=True)).reason_code == "legacy_client_ordinary_fallback"
    assert policy.resolve(request(browser=capability(codecs=frozenset({"video_av1"})))).safe_outcome == "ordinary_fallback"
    denied = policy.resolve(request(browser=capability(e2ee_compatible=False)))
    assert denied.safe_outcome == "deny" and denied.resolution == "unsupported"


def test_manipulated_large_capability_cannot_expand_administrative_profile():
    huge = capability(
        max_encodings=999, max_width_px=99_998, max_height_px=99_998, max_fps=999,
        max_aggregate_bitrate_bps=999_999_999, max_keyframe_interval_ms=999_999,
    )
    resolved = SfuBroadcastLayerProfilePolicy(PROFILE).resolve(request(browser=huge))
    assert resolved.limits is not None
    assert resolved.limits.max_encodings == 3
    assert resolved.limits.max_width_px == 1920
    assert resolved.limits.max_aggregate_bitrate_bps == 4_000_000


def test_profile_migration_and_boundary_drift_are_fail_closed(tmp_path: Path):
    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SfuBroadcastLayerProfileError, match="layer_profile_schema_unsupported"):
        SfuBroadcastLayerProfilePolicy(path).resolve(request())
    raw["schema_version"] = 1
    raw["profiles"]["camera"]["max_encodings"] = 4
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SfuBroadcastLayerProfileError, match="layer_profile_encodings_invalid"):
        SfuBroadcastLayerProfilePolicy(path).resolve(request())
