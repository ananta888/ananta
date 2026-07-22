"""Issue bounded transition strategies and provide a deterministic adapter-local reference."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from agent.services.sfu_receiver_layer_policy import (
    LayerCorridor,
    LayerPoint,
    ReceiverLayerPolicySignerPort,
    SfuReceiverLayerPolicyError,
)

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _ROOT / "config" / "sfu_broadcast_layer_transition_profiles.json"
_ROOT_KEYS = frozenset({"schema", "schema_version", "profile_revision", "hard_limits", "profiles"})
_HARD_KEYS = frozenset({
    "dwell_ms_min", "dwell_ms_max", "cooldown_ms_min", "cooldown_ms_max",
    "keyframe_wait_ms_min", "keyframe_wait_ms_max", "keyframe_retry_max",
    "recovery_deadline_ms_max", "transitions_per_minute_max", "keyframes_per_minute_max",
    "ttl_seconds_max",
})
_PROFILE_KEYS = frozenset({
    "profile_version", "upgrade_score_threshold", "downgrade_score_threshold", "upgrade_dwell_ms",
    "downgrade_dwell_ms", "upgrade_cooldown_ms", "downgrade_cooldown_ms", "keyframe_wait_ms",
    "keyframe_retry_max", "recovery_deadline_ms", "transitions_per_minute_max",
    "keyframes_per_minute_max", "ttl_seconds",
})


class SfuLayerTransitionProfileError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LayerTransitionProfileRequest:
    tenant_ref: str
    room_ref: str
    subscriber_ref: str
    subscription_ref: str
    publication_ref: str
    allowed_layer_corridor: LayerCorridor
    route_epoch: int
    key_epoch: int
    issued_at_ms: int
    profile_id: str = "receiver-default-v1"


@dataclass(frozen=True, slots=True)
class SignedLayerTransitionProfile:
    unsigned_payload: dict[str, Any]
    key_id: str
    signature: str

    def payload(self) -> dict[str, Any]:
        return {
            **self.unsigned_payload,
            "signature": {"algorithm": "HS256", "key_id": self.key_id, "value": self.signature},
        }

    @property
    def corridor(self) -> LayerCorridor:
        row = self.unsigned_payload["allowed_layer_corridor"]
        return LayerCorridor(LayerPoint(**row["minimum"]), LayerPoint(**row["maximum"]))

    @property
    def strategy(self) -> Mapping[str, Any]:
        return self.unsigned_payload["strategy"]


class SfuLayerTransitionProfilePolicy:
    """Hub configuration authority; it never receives packet or frame callbacks."""

    def __init__(
        self,
        signer: ReceiverLayerPolicySignerPort,
        profile_path: str | Path = _DEFAULT_PATH,
    ) -> None:
        self._signer = signer
        self._path = Path(profile_path)
        self._lock = threading.Lock()
        self._config: Mapping[str, Any] | None = None

    def issue(self, request: LayerTransitionProfileRequest) -> SignedLayerTransitionProfile:
        _validate_request(request)
        config = self._load()
        profiles = config["profiles"]
        if request.profile_id not in profiles:
            raise SfuLayerTransitionProfileError("transition_profile_unknown")
        configured = dict(profiles[request.profile_id])
        single_layer = request.allowed_layer_corridor.minimum == request.allowed_layer_corridor.maximum
        strategy = {
            key: value for key, value in configured.items()
            if key not in {"profile_version", "ttl_seconds"}
        }
        strategy["layer_changes_enabled"] = not single_layer
        if single_layer:
            strategy["keyframe_retry_max"] = 0
            strategy["transitions_per_minute_max"] = 0
            strategy["keyframes_per_minute_max"] = 0
        ttl_seconds = configured["ttl_seconds"]
        payload: dict[str, Any] = {
            "schema": "ananta.sfu-layer-transition-profile.v1",
            "schema_version": 1,
            "domain": "sfu_broadcast.layer_transition_profile.v1",
            "profile_id": request.profile_id,
            "profile_version": configured["profile_version"],
            "profile_revision": config["profile_revision"],
            "tenant_ref": request.tenant_ref,
            "room_ref": request.room_ref,
            "subscriber_ref": request.subscriber_ref,
            "subscription_ref": request.subscription_ref,
            "publication_ref": request.publication_ref,
            "allowed_layer_corridor": request.allowed_layer_corridor.payload(),
            "strategy": strategy,
            "route_epoch": request.route_epoch,
            "key_epoch": request.key_epoch,
            "issued_at_ms": request.issued_at_ms,
            "expires_at_ms": request.issued_at_ms + ttl_seconds * 1000,
            "ttl_seconds": ttl_seconds,
            "authorization_effect": "narrow_only",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return SignedLayerTransitionProfile(payload, self._signer.key_id, self._signer.sign(canonical))

    @staticmethod
    def validate_scope(
        profile: SignedLayerTransitionProfile,
        *,
        route_epoch: int,
        key_epoch: int,
        now_ms: int,
    ) -> str:
        row = profile.unsigned_payload
        if route_epoch != row["route_epoch"]:
            return "transition_profile_stale_route_epoch"
        if key_epoch != row["key_epoch"]:
            return "transition_profile_stale_key_epoch"
        if now_ms < row["issued_at_ms"] or now_ms >= row["expires_at_ms"]:
            return "transition_profile_expired"
        return "ok"

    def _load(self) -> Mapping[str, Any]:
        if self._config is not None:
            return self._config
        with self._lock:
            if self._config is None:
                self._config = _load_config(self._path)
            return self._config


@dataclass(frozen=True, slots=True)
class LocalLayerTransitionState:
    current: LayerPoint
    candidate: LayerPoint | None = None
    candidate_since_ms: int | None = None
    last_transition_ms: int | None = None
    transition_times_ms: tuple[int, ...] = ()
    recovery_started_ms: int | None = None
    last_keyframe_request_ms: int | None = None
    keyframe_attempts: int = 0
    keyframe_times_ms: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalLayerTransitionResult:
    state: LocalLayerTransitionState
    action: str
    request_keyframe: bool
    reason_code: str


class BoundedLocalLayerTransitionController:
    """Reference for browser/SFU adapters; no Hub I/O and no media callbacks."""

    def step(
        self,
        profile: SignedLayerTransitionProfile,
        state: LocalLayerTransitionState,
        *,
        desired: LayerPoint,
        quality_score: int,
        route_epoch: int,
        key_epoch: int,
        now_ms: int,
    ) -> LocalLayerTransitionResult:
        scope = SfuLayerTransitionProfilePolicy.validate_scope(
            profile, route_epoch=route_epoch, key_epoch=key_epoch, now_ms=now_ms,
        )
        if scope != "ok":
            return LocalLayerTransitionResult(state, "reject", False, scope)
        if type(quality_score) is not int or not 0 <= quality_score <= 1000:
            return LocalLayerTransitionResult(state, "reject", False, "transition_quality_score_invalid")
        if not profile.corridor.contains(desired):
            return LocalLayerTransitionResult(state, "reject", False, "transition_target_outside_corridor")
        strategy = profile.strategy
        state = _prune_rates(state, now_ms)
        if not strategy["layer_changes_enabled"]:
            return LocalLayerTransitionResult(state, "hold", False, "transition_single_layer_hold")
        if desired == state.current:
            return self._recovery_step(profile, replace(state, candidate=None, candidate_since_ms=None), now_ms)
        upgrade = _rank(desired) > _rank(state.current)
        threshold_met = quality_score >= strategy["upgrade_score_threshold"] if upgrade \
            else quality_score <= strategy["downgrade_score_threshold"]
        if not threshold_met:
            return LocalLayerTransitionResult(
                replace(state, candidate=None, candidate_since_ms=None), "hold", False,
                "transition_threshold_not_met",
            )
        if state.candidate != desired or state.candidate_since_ms is None:
            return LocalLayerTransitionResult(
                replace(state, candidate=desired, candidate_since_ms=now_ms), "hold", False,
                "transition_dwell_started",
            )
        dwell = strategy["upgrade_dwell_ms"] if upgrade else strategy["downgrade_dwell_ms"]
        cooldown = strategy["upgrade_cooldown_ms"] if upgrade else strategy["downgrade_cooldown_ms"]
        if now_ms - state.candidate_since_ms < dwell:
            return LocalLayerTransitionResult(state, "hold", False, "transition_dwell_pending")
        if state.last_transition_ms is not None and now_ms - state.last_transition_ms < cooldown:
            return LocalLayerTransitionResult(state, "hold", False, "transition_cooldown_pending")
        if len(state.transition_times_ms) >= strategy["transitions_per_minute_max"]:
            return LocalLayerTransitionResult(state, "hold", False, "transition_rate_limited")
        next_state = replace(
            state, current=desired, candidate=None, candidate_since_ms=None, last_transition_ms=now_ms,
            transition_times_ms=(*state.transition_times_ms, now_ms),
            recovery_started_ms=now_ms if upgrade else None,
            last_keyframe_request_ms=None, keyframe_attempts=0, keyframe_times_ms=(),
        )
        if not upgrade:
            return LocalLayerTransitionResult(next_state, "downshift", False, "transition_downshift_applied")
        return self._request_keyframe(profile, next_state, now_ms, action="upshift")

    def acknowledge_keyframe(
        self,
        profile: SignedLayerTransitionProfile,
        state: LocalLayerTransitionState,
        *,
        route_epoch: int,
        key_epoch: int,
        now_ms: int,
    ) -> LocalLayerTransitionResult:
        scope = SfuLayerTransitionProfilePolicy.validate_scope(
            profile, route_epoch=route_epoch, key_epoch=key_epoch, now_ms=now_ms,
        )
        if scope != "ok":
            return LocalLayerTransitionResult(state, "reject", False, scope)
        return LocalLayerTransitionResult(
            replace(state, recovery_started_ms=None, last_keyframe_request_ms=None, keyframe_attempts=0),
            "ack", False, "transition_keyframe_acknowledged",
        )

    def _recovery_step(
        self,
        profile: SignedLayerTransitionProfile,
        state: LocalLayerTransitionState,
        now_ms: int,
    ) -> LocalLayerTransitionResult:
        strategy = profile.strategy
        if state.recovery_started_ms is None:
            return LocalLayerTransitionResult(state, "hold", False, "transition_layer_stable")
        if now_ms - state.recovery_started_ms >= strategy["recovery_deadline_ms"]:
            return LocalLayerTransitionResult(
                replace(state, recovery_started_ms=None), "fallback", False,
                "transition_recovery_deadline_exceeded",
            )
        if state.last_keyframe_request_ms is not None \
                and now_ms - state.last_keyframe_request_ms < strategy["keyframe_wait_ms"]:
            return LocalLayerTransitionResult(state, "hold", False, "transition_keyframe_wait")
        return self._request_keyframe(profile, state, now_ms, action="hold")

    @staticmethod
    def _request_keyframe(
        profile: SignedLayerTransitionProfile,
        state: LocalLayerTransitionState,
        now_ms: int,
        *,
        action: str,
    ) -> LocalLayerTransitionResult:
        strategy = profile.strategy
        if state.keyframe_attempts >= strategy["keyframe_retry_max"] \
                or len(state.keyframe_times_ms) >= strategy["keyframes_per_minute_max"]:
            return LocalLayerTransitionResult(state, action, False, "transition_keyframe_limit_reached")
        next_state = replace(
            state, last_keyframe_request_ms=now_ms, keyframe_attempts=state.keyframe_attempts + 1,
            keyframe_times_ms=(*state.keyframe_times_ms, now_ms),
        )
        return LocalLayerTransitionResult(next_state, action, True, "transition_keyframe_requested")


def _load_config(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuLayerTransitionProfileError("transition_profile_unavailable") from exc
    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS \
            or raw["schema"] != "ananta.webrtc.sfu-layer-transition-profiles.v1" \
            or raw["schema_version"] != 1:
        raise SfuLayerTransitionProfileError("transition_profile_root_invalid")
    _positive(raw["profile_revision"], "transition_profile_revision_invalid")
    hard = raw["hard_limits"]
    profiles = raw["profiles"]
    if not isinstance(hard, dict) or set(hard) != _HARD_KEYS or not isinstance(profiles, dict) or not profiles:
        raise SfuLayerTransitionProfileError("transition_profile_limits_invalid")
    for profile_id, profile in profiles.items():
        if not isinstance(profile_id, str) or not profile_id or not isinstance(profile, dict) or set(profile) != _PROFILE_KEYS:
            raise SfuLayerTransitionProfileError("transition_profile_fields_invalid")
        _validate_configured_profile(profile, hard)
    return raw


def _validate_configured_profile(profile: Mapping[str, Any], hard: Mapping[str, Any]) -> None:
    if profile["profile_version"] != "receiver-transition-v1":
        raise SfuLayerTransitionProfileError("transition_profile_version_invalid")
    up = _bounded(profile["upgrade_score_threshold"], 1, 1000, "transition_up_threshold_invalid")
    down = _bounded(profile["downgrade_score_threshold"], 0, 999, "transition_down_threshold_invalid")
    if down >= up:
        raise SfuLayerTransitionProfileError("transition_threshold_order_invalid")
    for prefix in ("upgrade", "downgrade"):
        _bounded(profile[f"{prefix}_dwell_ms"], hard["dwell_ms_min"], hard["dwell_ms_max"], "transition_dwell_invalid")
        _bounded(profile[f"{prefix}_cooldown_ms"], hard["cooldown_ms_min"], hard["cooldown_ms_max"], "transition_cooldown_invalid")
    _bounded(profile["keyframe_wait_ms"], hard["keyframe_wait_ms_min"], hard["keyframe_wait_ms_max"], "transition_keyframe_wait_invalid")
    _bounded(profile["keyframe_retry_max"], 0, hard["keyframe_retry_max"], "transition_keyframe_retry_invalid")
    _bounded(profile["recovery_deadline_ms"], hard["keyframe_wait_ms_min"], hard["recovery_deadline_ms_max"], "transition_recovery_invalid")
    _bounded(profile["transitions_per_minute_max"], 1, hard["transitions_per_minute_max"], "transition_rate_invalid")
    _bounded(profile["keyframes_per_minute_max"], 1, hard["keyframes_per_minute_max"], "transition_keyframe_rate_invalid")
    _bounded(profile["ttl_seconds"], 1, hard["ttl_seconds_max"], "transition_ttl_invalid")


def _validate_request(request: LayerTransitionProfileRequest) -> None:
    for name in ("tenant_ref", "room_ref", "subscriber_ref", "subscription_ref", "publication_ref", "profile_id"):
        value = getattr(request, name)
        if not isinstance(value, str) or not value or len(value) > 128 or any(character.isspace() for character in value):
            raise SfuLayerTransitionProfileError(f"transition_{name}_invalid")
    for name in ("route_epoch", "key_epoch"):
        _positive(getattr(request, name), f"transition_{name}_invalid")
    if type(request.issued_at_ms) is not int or request.issued_at_ms < 0:
        raise SfuLayerTransitionProfileError("transition_issued_at_invalid")


def _prune_rates(state: LocalLayerTransitionState, now_ms: int) -> LocalLayerTransitionState:
    cutoff = now_ms - 60_000
    return replace(
        state,
        transition_times_ms=tuple(value for value in state.transition_times_ms if value > cutoff),
        keyframe_times_ms=tuple(value for value in state.keyframe_times_ms if value > cutoff),
    )


def _rank(point: LayerPoint) -> int:
    return point.spatial_id * 4 + point.temporal_id


def _bounded(value: object, minimum: object, maximum: object, reason: str) -> int:
    if type(value) is not int or type(minimum) is not int or type(maximum) is not int \
            or not minimum <= value <= maximum:
        raise SfuLayerTransitionProfileError(reason)
    return value


def _positive(value: object, reason: str) -> int:
    return _bounded(value, 1, 2_147_483_647, reason)


__all__ = [
    "BoundedLocalLayerTransitionController", "LayerTransitionProfileRequest",
    "LocalLayerTransitionResult", "LocalLayerTransitionState", "SfuLayerTransitionProfileError",
    "SfuLayerTransitionProfilePolicy", "SignedLayerTransitionProfile",
]
