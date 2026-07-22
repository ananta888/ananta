"""Hub-owned bounded codec and layer-profile intersection policy."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _ROOT / "config" / "sfu_broadcast_layer_profiles.json"
_SCHEMA = "ananta.webrtc.sfu-broadcast-layer-profiles.v1"
_MEDIA_KINDS = frozenset({"audio", "camera", "screenshare", "semantic_reference"})
_CODECS = frozenset({"audio_opus", "video_vp8", "video_h264", "video_vp9", "video_av1"})
_SCALABILITY = frozenset({"none", "simulcast", "svc_l1t2", "svc_l1t3", "svc_l2t2", "svc_l3t3"})
_FALLBACKS = frozenset({"ordinary_fallback", "deny"})
_ROOT_KEYS = frozenset({
    "schema", "schema_version", "profile_revision", "publisher_profile_version",
    "receiver_profile_version", "profiles",
})
_PROFILE_KEYS = frozenset({
    "profile_id", "codec_classes", "scalability_classes", "max_encodings", "max_width_px",
    "max_height_px", "max_fps", "max_aggregate_bitrate_bps", "max_keyframe_interval_ms",
    "fallbacks", "e2ee_required",
})


class SfuBroadcastLayerProfileError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LayerSourceCapability:
    known: bool
    codec_classes: frozenset[str] = frozenset()
    scalability_classes: frozenset[str] = frozenset()
    max_encodings: int | None = None
    max_width_px: int | None = None
    max_height_px: int | None = None
    max_fps: int | None = None
    max_aggregate_bitrate_bps: int | None = None
    max_keyframe_interval_ms: int | None = None
    e2ee_compatible: bool | None = None

    @classmethod
    def unknown(cls) -> "LayerSourceCapability":
        return cls(known=False)


@dataclass(frozen=True, slots=True)
class LayerProfileRequest:
    media_kind: str
    parent_publication: LayerSourceCapability
    browser: LayerSourceCapability
    sfu: LayerSourceCapability
    receiver: LayerSourceCapability
    e2ee_required: bool = True
    legacy_client: bool = False


@dataclass(frozen=True, slots=True)
class EffectiveLayerLimits:
    codec_classes: tuple[str, ...]
    scalability_classes: tuple[str, ...]
    max_encodings: int
    max_width_px: int
    max_height_px: int
    max_fps: int
    max_aggregate_bitrate_bps: int
    max_keyframe_interval_ms: int

    def payload(self) -> dict[str, Any]:
        return {
            "codec_classes": list(self.codec_classes),
            "scalability_classes": list(self.scalability_classes),
            "max_encodings": self.max_encodings,
            "max_width_px": self.max_width_px,
            "max_height_px": self.max_height_px,
            "max_fps": self.max_fps,
            "max_aggregate_bitrate_bps": self.max_aggregate_bitrate_bps,
            "max_keyframe_interval_ms": self.max_keyframe_interval_ms,
        }


@dataclass(frozen=True, slots=True)
class ResolvedLayerProfile:
    profile_id: str
    profile_revision: int
    profile_digest: str
    publisher_profile_version: str
    receiver_profile_version: str
    resolution: str
    safe_outcome: str
    reason_code: str
    limits: EffectiveLayerLimits | None
    authorization_effect: str = "narrow_only"


@dataclass(frozen=True, slots=True)
class _AdministrativeProfile:
    profile_id: str
    codecs: frozenset[str]
    scalability: frozenset[str]
    maximums: tuple[int, int, int, int, int, int]
    fallbacks: tuple[str, ...]
    e2ee_required: bool


class SfuBroadcastLayerProfilePolicy:
    """Resolve a pure intersection; browser observations never become authority."""

    def __init__(self, profile_path: str | Path = _DEFAULT_PATH) -> None:
        self._path = Path(profile_path)
        self._lock = threading.Lock()
        self._loaded: tuple[dict[str, _AdministrativeProfile], Mapping[str, Any], str] | None = None

    def resolve(self, request: LayerProfileRequest) -> ResolvedLayerProfile:
        profiles, root, digest = self._profiles()
        if request.media_kind not in profiles:
            raise SfuBroadcastLayerProfileError("layer_profile_media_kind_invalid")
        profile = profiles[request.media_kind]
        sources = (request.parent_publication, request.browser, request.sfu, request.receiver)
        for source in sources:
            _validate_source(source, request.media_kind)
        if request.legacy_client:
            return self._fallback(profile, root, digest, "legacy_client_ordinary_fallback")
        if any(not source.known for source in sources):
            return self._fallback(profile, root, digest, "layer_capability_unknown")
        if request.e2ee_required or profile.e2ee_required:
            if any(source.e2ee_compatible is False for source in sources):
                return self._fallback(profile, root, digest, "layer_e2ee_incompatible", force_deny=True)
            if any(source.e2ee_compatible is not True for source in sources):
                return self._fallback(profile, root, digest, "layer_e2ee_capability_unknown")
        codecs = set(profile.codecs)
        scalability = set(profile.scalability)
        for source in sources:
            codecs.intersection_update(source.codec_classes)
            scalability.intersection_update(source.scalability_classes)
        if not codecs or not scalability:
            return self._fallback(profile, root, digest, "layer_capability_intersection_empty")
        maximum_columns = tuple(zip(profile.maximums, *(_source_maximums(source) for source in sources)))
        values = tuple(min(column) for column in maximum_columns)
        if values[0] < 1 or values[4] < 1:
            return self._fallback(profile, root, digest, "layer_numeric_intersection_empty")
        if request.media_kind != "audio" and any(value < 1 for value in (values[1], values[2], values[3], values[5])):
            return self._fallback(profile, root, digest, "layer_video_intersection_empty")
        limits = EffectiveLayerLimits(
            codec_classes=tuple(sorted(codecs)),
            scalability_classes=tuple(sorted(scalability)),
            max_encodings=values[0], max_width_px=values[1], max_height_px=values[2],
            max_fps=values[3], max_aggregate_bitrate_bps=values[4],
            max_keyframe_interval_ms=values[5],
        )
        return ResolvedLayerProfile(
            profile.profile_id, int(root["profile_revision"]), digest,
            str(root["publisher_profile_version"]), str(root["receiver_profile_version"]),
            "planned", "apply_projection", "layer_profile_intersection_applied", limits,
        )

    def _fallback(
        self,
        profile: _AdministrativeProfile,
        root: Mapping[str, Any],
        digest: str,
        reason: str,
        *,
        force_deny: bool = False,
    ) -> ResolvedLayerProfile:
        outcome = "deny" if force_deny or "ordinary_fallback" not in profile.fallbacks else "ordinary_fallback"
        resolution = "unsupported" if force_deny else "unknown"
        return ResolvedLayerProfile(
            profile.profile_id, int(root["profile_revision"]), digest,
            str(root["publisher_profile_version"]), str(root["receiver_profile_version"]),
            resolution, outcome, reason, None,
        )

    def _profiles(self) -> tuple[dict[str, _AdministrativeProfile], Mapping[str, Any], str]:
        if self._loaded is not None:
            return self._loaded
        with self._lock:
            if self._loaded is None:
                self._loaded = self._load()
            return self._loaded

    def _load(self) -> tuple[dict[str, _AdministrativeProfile], Mapping[str, Any], str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SfuBroadcastLayerProfileError("layer_profile_unavailable") from exc
        if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
            raise SfuBroadcastLayerProfileError("layer_profile_root_invalid")
        if raw["schema"] != _SCHEMA or raw["schema_version"] != 1:
            raise SfuBroadcastLayerProfileError("layer_profile_schema_unsupported")
        _positive(raw["profile_revision"], "layer_profile_revision_invalid")
        if raw["publisher_profile_version"] != "publisher-layer-profile-v1" \
                or raw["receiver_profile_version"] != "receiver-layer-profile-v1":
            raise SfuBroadcastLayerProfileError("layer_profile_contract_version_invalid")
        rows = raw["profiles"]
        if not isinstance(rows, dict) or set(rows) != _MEDIA_KINDS:
            raise SfuBroadcastLayerProfileError("layer_profile_media_set_invalid")
        profiles = {kind: _parse_profile(kind, value) for kind, value in rows.items()}
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return profiles, raw, hashlib.sha256(canonical).hexdigest()


def _parse_profile(kind: str, value: object) -> _AdministrativeProfile:
    if not isinstance(value, dict) or set(value) != _PROFILE_KEYS:
        raise SfuBroadcastLayerProfileError("layer_profile_fields_invalid")
    profile_id = value["profile_id"]
    if not isinstance(profile_id, str) or not profile_id or len(profile_id) > 128:
        raise SfuBroadcastLayerProfileError("layer_profile_id_invalid")
    codecs = _closed_strings(value["codec_classes"], _CODECS, "layer_profile_codecs_invalid")
    modes = _closed_strings(value["scalability_classes"], _SCALABILITY, "layer_profile_scalability_invalid")
    fallbacks = tuple(_closed_strings(value["fallbacks"], _FALLBACKS, "layer_profile_fallback_invalid"))
    maximums = (
        _bounded(value["max_encodings"], 1, 3, "layer_profile_encodings_invalid"),
        _bounded(value["max_width_px"], 0, 3840, "layer_profile_width_invalid"),
        _bounded(value["max_height_px"], 0, 2160, "layer_profile_height_invalid"),
        _bounded(value["max_fps"], 0, 60, "layer_profile_fps_invalid"),
        _bounded(value["max_aggregate_bitrate_bps"], 6000, 8_000_000, "layer_profile_bitrate_invalid"),
        _bounded(value["max_keyframe_interval_ms"], 0, 60_000, "layer_profile_keyframe_invalid"),
    )
    if kind == "audio":
        if codecs != frozenset({"audio_opus"}) or modes != frozenset({"none"}) or any(maximums[index] for index in (1, 2, 3, 5)):
            raise SfuBroadcastLayerProfileError("layer_profile_audio_invalid")
    elif "audio_opus" in codecs or any(maximums[index] == 0 for index in (1, 2, 3, 5)):
        raise SfuBroadcastLayerProfileError("layer_profile_video_invalid")
    if type(value["e2ee_required"]) is not bool:
        raise SfuBroadcastLayerProfileError("layer_profile_e2ee_invalid")
    return _AdministrativeProfile(profile_id, codecs, modes, maximums, fallbacks, value["e2ee_required"])


def _validate_source(source: LayerSourceCapability, media_kind: str) -> None:
    if type(source.known) is not bool or source.e2ee_compatible not in (True, False, None):
        raise SfuBroadcastLayerProfileError("layer_capability_shape_invalid")
    if not source.known:
        return
    if not source.codec_classes or not source.scalability_classes:
        raise SfuBroadcastLayerProfileError("layer_capability_set_empty")
    values = _source_maximums(source)
    if values[0] < 1 or values[4] < 1 or any(value < 0 for value in values):
        raise SfuBroadcastLayerProfileError("layer_capability_limit_invalid")
    if media_kind != "audio" and any(values[index] < 1 for index in (1, 2, 3, 5)):
        raise SfuBroadcastLayerProfileError("layer_capability_video_limit_invalid")


def _source_maximums(source: LayerSourceCapability) -> tuple[int, int, int, int, int, int]:
    values = (
        source.max_encodings, source.max_width_px, source.max_height_px, source.max_fps,
        source.max_aggregate_bitrate_bps, source.max_keyframe_interval_ms,
    )
    if any(type(value) is not int for value in values):
        raise SfuBroadcastLayerProfileError("layer_capability_limit_missing")
    return values  # type: ignore[return-value]


def _closed_strings(value: object, allowed: frozenset[str], reason: str) -> frozenset[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise SfuBroadcastLayerProfileError(reason)
    result = frozenset(value)
    if len(result) != len(value) or not result <= allowed:
        raise SfuBroadcastLayerProfileError(reason)
    return result


def _bounded(value: object, minimum: int, maximum: int, reason: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SfuBroadcastLayerProfileError(reason)
    return value


def _positive(value: object, reason: str) -> int:
    return _bounded(value, 1, 2_147_483_647, reason)


__all__ = [
    "EffectiveLayerLimits", "LayerProfileRequest", "LayerSourceCapability", "ResolvedLayerProfile",
    "SfuBroadcastLayerProfileError", "SfuBroadcastLayerProfilePolicy",
]
