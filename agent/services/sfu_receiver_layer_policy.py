"""Pure Hub policy for receiver-specific, narrow-only layer corridors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VIEWPORT_CAPS = {
    "hidden": (0, 0), "thumbnail": (0, 1), "standard": (1, 2), "large": (3, 3),
}
_INTENT_CAPS = {
    "audio_only": (0, 0), "data_saver": (0, 1), "balanced": (1, 2), "detail": (3, 3),
}
_QUALITY_CAPS = {
    "unknown": (0, 0), "degraded": (0, 1), "stable": (1, 2), "good": (3, 3),
}


class SfuReceiverLayerPolicyError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, order=True, slots=True)
class LayerPoint:
    spatial_id: int
    temporal_id: int

    def __post_init__(self) -> None:
        if type(self.spatial_id) is not int or type(self.temporal_id) is not int \
                or not 0 <= self.spatial_id <= 3 or not 0 <= self.temporal_id <= 3:
            raise SfuReceiverLayerPolicyError("receiver_layer_point_invalid")

    def payload(self) -> dict[str, int]:
        return {"spatial_id": self.spatial_id, "temporal_id": self.temporal_id}


@dataclass(frozen=True, slots=True)
class LayerCorridor:
    minimum: LayerPoint
    maximum: LayerPoint

    def __post_init__(self) -> None:
        if self.minimum.spatial_id > self.maximum.spatial_id \
                or self.minimum.temporal_id > self.maximum.temporal_id:
            raise SfuReceiverLayerPolicyError("receiver_layer_corridor_invalid")

    def intersect(self, other: "LayerCorridor") -> "LayerCorridor | None":
        minimum = LayerPoint(
            max(self.minimum.spatial_id, other.minimum.spatial_id),
            max(self.minimum.temporal_id, other.minimum.temporal_id),
        )
        maximum = LayerPoint(
            min(self.maximum.spatial_id, other.maximum.spatial_id),
            min(self.maximum.temporal_id, other.maximum.temporal_id),
        )
        if minimum.spatial_id > maximum.spatial_id or minimum.temporal_id > maximum.temporal_id:
            return None
        return LayerCorridor(minimum, maximum)

    def contains(self, point: LayerPoint) -> bool:
        return self.minimum.spatial_id <= point.spatial_id <= self.maximum.spatial_id \
            and self.minimum.temporal_id <= point.temporal_id <= self.maximum.temporal_id

    def payload(self) -> dict[str, Any]:
        return {"minimum": self.minimum.payload(), "maximum": self.maximum.payload()}


class ReceiverLayerPolicySignerPort(Protocol):
    @property
    def key_id(self) -> str: ...

    def sign(self, canonical_payload: bytes) -> str: ...


class HmacReceiverLayerPolicySigner:
    """Small signing adapter; production may substitute an asymmetric signer."""

    def __init__(self, secret: bytes, *, key_id: str) -> None:
        if len(secret) < 32:
            raise SfuReceiverLayerPolicyError("receiver_layer_signing_key_too_short")
        _id(key_id, "receiver_layer_signing_key_id_invalid")
        self._secret = bytes(secret)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, canonical_payload: bytes) -> str:
        digest = hmac.new(self._secret, canonical_payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class ReceiverLayerPolicyRequest:
    tenant_ref: str
    room_ref: str
    subscriber_ref: str
    subscription_ref: str
    publication_ref: str
    media_kind: str
    hub_corridor: LayerCorridor
    publication_corridor: LayerCorridor
    e2ee_corridor: LayerCorridor
    codec_corridor: LayerCorridor
    cost_corridor: LayerCorridor
    capacity_corridor: LayerCorridor
    viewport_class: str
    user_intent: str
    quality_class: str
    last_observation_sequence: int | None
    issued_at_ms: int


@dataclass(frozen=True, slots=True)
class SignedReceiverLayerDecision:
    unsigned_payload: dict[str, Any]
    key_id: str
    signature: str

    def payload(self) -> dict[str, Any]:
        return {
            **self.unsigned_payload,
            "signature": {"algorithm": "HS256", "key_id": self.key_id, "value": self.signature},
        }


class SfuReceiverLayerPolicy:
    """Stateless policy: each call can affect only the named subscription."""

    def __init__(
        self,
        signer: ReceiverLayerPolicySignerPort,
        *,
        ttl_ms: int = 10_000,
        reevaluate_not_before_ms: int = 3_000,
    ) -> None:
        if not 1_000 <= ttl_ms <= 30_000 or not 250 <= reevaluate_not_before_ms <= ttl_ms:
            raise SfuReceiverLayerPolicyError("receiver_layer_policy_timing_invalid")
        self._signer = signer
        self._ttl_ms = ttl_ms
        self._reevaluate_delay_ms = reevaluate_not_before_ms

    def decide(self, request: ReceiverLayerPolicyRequest) -> SignedReceiverLayerDecision:
        _validate_request(request)
        corridor: LayerCorridor | None = request.hub_corridor
        for authority in (
            request.publication_corridor, request.e2ee_corridor, request.codec_corridor,
            request.cost_corridor, request.capacity_corridor,
        ):
            corridor = corridor.intersect(authority) if corridor is not None else None
        reason = "receiver_layer_policy_applied"
        outcome = "applied_within_corridor"
        if request.media_kind == "audio":
            corridor = _intersect_cap(corridor, (0, 0))
            reason = "receiver_audio_single_layer"
            outcome = "lowest_safe_layer"
        elif request.user_intent == "audio_only":
            corridor = None
            reason = "receiver_audio_only_video_denied"
            outcome = "deny"
        else:
            corridor = _intersect_cap(corridor, _VIEWPORT_CAPS[request.viewport_class])
            corridor = _intersect_cap(corridor, _INTENT_CAPS[request.user_intent])
            corridor = _intersect_cap(corridor, _QUALITY_CAPS[request.quality_class])
            if request.viewport_class == "hidden":
                reason, outcome = "receiver_hidden_lowest_safe", "lowest_safe_layer"
            elif request.quality_class in {"unknown", "degraded"}:
                reason, outcome = "receiver_quality_conservative", "lowest_safe_layer"
        if corridor is None and outcome != "deny":
            reason, outcome = "receiver_layer_intersection_empty", "ordinary_fallback"
        payload: dict[str, Any] = {
            "schema": "ananta.sfu-receiver-layer-policy-decision.v1",
            "profile_version": "receiver-layer-profile-v1",
            "tenant_ref": request.tenant_ref,
            "room_ref": request.room_ref,
            "subscriber_ref": request.subscriber_ref,
            "subscription_ref": request.subscription_ref,
            "publication_ref": request.publication_ref,
            "media_kind": request.media_kind,
            "allowed_layer_corridor": None if corridor is None else corridor.payload(),
            "quality_basis": {
                "quality_version": "bounded-v1",
                "last_observation_sequence": request.last_observation_sequence,
            },
            "safe_outcome": outcome,
            "reason_code": reason,
            "issued_at_ms": request.issued_at_ms,
            "expires_at_ms": request.issued_at_ms + self._ttl_ms,
            "ttl_ms": self._ttl_ms,
            "reevaluate_not_before_ms": request.issued_at_ms + self._reevaluate_delay_ms,
            "authorization_effect": "narrow_only",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return SignedReceiverLayerDecision(payload, self._signer.key_id, self._signer.sign(canonical))


def _intersect_cap(corridor: LayerCorridor | None, cap: tuple[int, int]) -> LayerCorridor | None:
    if corridor is None:
        return None
    maximum = LayerPoint(min(corridor.maximum.spatial_id, cap[0]), min(corridor.maximum.temporal_id, cap[1]))
    if corridor.minimum.spatial_id > maximum.spatial_id or corridor.minimum.temporal_id > maximum.temporal_id:
        return None
    return LayerCorridor(corridor.minimum, maximum)


def _validate_request(request: ReceiverLayerPolicyRequest) -> None:
    for field in ("tenant_ref", "room_ref", "subscriber_ref", "subscription_ref", "publication_ref"):
        _id(getattr(request, field), f"receiver_layer_{field}_invalid")
    if request.media_kind not in {"audio", "video", "screenshare"}:
        raise SfuReceiverLayerPolicyError("receiver_layer_media_kind_invalid")
    if request.viewport_class not in _VIEWPORT_CAPS:
        raise SfuReceiverLayerPolicyError("receiver_layer_viewport_invalid")
    if request.user_intent not in _INTENT_CAPS:
        raise SfuReceiverLayerPolicyError("receiver_layer_user_intent_invalid")
    if request.quality_class not in _QUALITY_CAPS:
        raise SfuReceiverLayerPolicyError("receiver_layer_quality_invalid")
    if type(request.issued_at_ms) is not int or request.issued_at_ms < 0:
        raise SfuReceiverLayerPolicyError("receiver_layer_issued_at_invalid")
    if request.last_observation_sequence is not None and (
        type(request.last_observation_sequence) is not int or not 1 <= request.last_observation_sequence <= 2_147_483_647
    ):
        raise SfuReceiverLayerPolicyError("receiver_layer_observation_sequence_invalid")


def _id(value: object, reason: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SfuReceiverLayerPolicyError(reason)
    return value


__all__ = [
    "HmacReceiverLayerPolicySigner", "LayerCorridor", "LayerPoint", "ReceiverLayerPolicyRequest",
    "ReceiverLayerPolicySignerPort", "SfuReceiverLayerPolicy", "SfuReceiverLayerPolicyError",
    "SignedReceiverLayerDecision",
]
