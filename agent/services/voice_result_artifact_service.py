from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any, Mapping

from agent.repositories.voice_result_artifact import VoiceResultArtifactRepository
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal, validate_identifier
from agent.services.voice_sensitive_text_codec import VoiceSensitiveTextCodec, get_voice_sensitive_text_codec


class VoiceResultArtifactService:
    """Stores immutable, encrypted transcript results without raw audio."""

    def __init__(
        self,
        repository: VoiceResultArtifactRepository | None = None,
        codec: VoiceSensitiveTextCodec | None = None,
        retention_resolver: Callable[[VoicePrincipal, str], int] | None = None,
    ) -> None:
        self._repository = repository or VoiceResultArtifactRepository()
        self._codec = codec or get_voice_sensitive_text_codec()
        self._retention_resolver = retention_resolver or _transcript_retention_seconds

    def create(
        self,
        principal: VoicePrincipal,
        *,
        request_hash: str,
        result: Mapping[str, Any],
        profile_id: str = "default",
        retention_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._repository.purge_expired()
        normalized_result = dict(result)
        _assert_no_raw_audio(normalized_result)
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        try:
            canonical_result = json.dumps(
                normalized_result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise VoiceGovernanceError(
                code="voice_result.invalid_payload",
                message="voice result artifact must contain finite JSON data",
                status_code=422,
            ) from exc
        if len(canonical_result.encode("utf-8")) > 2 * 1024 * 1024:
            raise VoiceGovernanceError(
                code="voice_result.too_large",
                message="voice result artifact exceeds its size budget",
                status_code=413,
            )
        candidate_ids = [
            str(item.get("candidate_id"))
            for item in (normalized_result.get("candidates") or [])
            if isinstance(item, dict) and item.get("candidate_id")
        ]
        policy_retention_seconds = self.retention_seconds_for(principal, normalized_profile_id)
        requested_retention_seconds = (
            policy_retention_seconds if retention_seconds is None else int(retention_seconds)
        )
        expires_at = time.time() + max(
            60,
            min(requested_retention_seconds, policy_retention_seconds, 30 * 24 * 60 * 60),
        )
        candidates_payload = {"candidates": list(normalized_result.get("candidates") or [])}
        fusion_payload = {key: value for key, value in normalized_result.items() if key != "candidates"}
        candidates_id = f"voice-result-{uuid.uuid4()}"
        fusion_id = f"voice-result-{uuid.uuid4()}"
        envelope_id = f"voice-result-{uuid.uuid4()}"
        envelope_payload = {
            "schema_version": "ananta.voice-result-envelope.v1",
            "candidates_ref": candidates_id,
            "fusion_ref": fusion_id,
        }
        common = {
            "request_hash": request_hash,
            "profile_id": normalized_profile_id,
            "candidate_ids": candidate_ids,
            "expires_at": expires_at,
        }
        candidates_artifact, fusion_artifact, artifact = self._repository.create_many(
            principal,
            artifacts=[
                {
                    **common,
                    "id": candidates_id,
                    "artifact_kind": "raw_candidates",
                    "parent_artifact_id": envelope_id,
                    **self._encode_payload(candidates_payload),
                },
                {
                    **common,
                    "id": fusion_id,
                    "artifact_kind": "fusion_result",
                    "parent_artifact_id": envelope_id,
                    **self._encode_payload(fusion_payload),
                },
                {
                    **common,
                    "id": envelope_id,
                    "artifact_kind": "result_envelope",
                    "parent_artifact_id": None,
                    **self._encode_payload(envelope_payload),
                },
            ],
        )
        return {
            "id": artifact.id,
            "profile_id": artifact.profile_id,
            "payload_digest": artifact.payload_digest,
            "candidate_ids": list(artifact.candidate_ids),
            "expires_at": artifact.expires_at,
            "candidates_ref": candidates_artifact.id,
            "fusion_ref": fusion_artifact.id,
        }

    def get(self, principal: VoicePrincipal, artifact_id: str) -> dict[str, Any]:
        self._repository.purge_expired()
        normalized_id = validate_identifier(artifact_id, field="result_ref", max_length=200)
        artifact = self._repository.get(principal, normalized_id)
        if artifact is None:
            raise VoiceGovernanceError(
                code="voice_result.not_found",
                message="voice result artifact not found",
                status_code=404,
            )
        if artifact.expires_at <= time.time():
            raise VoiceGovernanceError(
                code="voice_result.expired",
                message="voice result artifact expired",
                status_code=410,
            )
        payload = self._load_payload(artifact)
        if artifact.artifact_kind != "result_envelope":
            raise VoiceGovernanceError(
                code="voice_result.invalid_reference",
                message="voice result reference does not identify a result envelope",
                status_code=422,
            )
        candidates_ref = str(payload.get("candidates_ref") or "")
        fusion_ref = str(payload.get("fusion_ref") or "")
        candidates_artifact = self._repository.get(
            principal,
            candidates_ref,
            profile_id=artifact.profile_id,
        )
        fusion_artifact = self._repository.get(
            principal,
            fusion_ref,
            profile_id=artifact.profile_id,
        )
        if candidates_artifact is None or fusion_artifact is None:
            raise VoiceGovernanceError(
                code="voice_result.incomplete",
                message="voice result child artifact is missing",
                status_code=500,
            )
        candidates = self._load_payload(candidates_artifact)
        fusion = self._load_payload(fusion_artifact)
        result = {**fusion, "candidates": list(candidates.get("candidates") or [])}
        _assert_no_raw_audio(result)
        return {
            "id": artifact.id,
            "profile_id": artifact.profile_id,
            "result": result,
            "payload_digest": artifact.payload_digest,
            "candidate_ids": list(artifact.candidate_ids),
            "expires_at": artifact.expires_at,
            "candidates_ref": candidates_ref,
            "fusion_ref": fusion_ref,
        }

    def find_live_envelope(
        self,
        principal: VoicePrincipal,
        *,
        request_ref: str,
        profile_id: str = "default",
    ) -> dict[str, Any] | None:
        """Load a crash-recoverable envelope by opaque request reference."""

        self._repository.purge_expired()
        normalized_request_ref = validate_identifier(
            request_ref,
            field="request_ref",
            max_length=200,
        )
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        artifact = self._repository.find_live_envelope(
            principal,
            request_ref=normalized_request_ref,
            profile_id=normalized_profile_id,
        )
        if artifact is None:
            return None
        return self.get(principal, artifact.id)

    def delete_profile(self, principal: VoicePrincipal, profile_id: str) -> int:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        deleted_count: int = self._repository.delete_profile(principal, normalized_profile_id)
        return deleted_count

    def retention_seconds_for(self, principal: VoicePrincipal, profile_id: str) -> int:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        resolved = int(self._retention_resolver(principal, normalized_profile_id))
        return max(60, min(resolved, 30 * 24 * 60 * 60))

    def _encode_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {
            "payload_ciphertext": str(self._codec.encrypt(canonical)),
            "payload_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }

    def _load_payload(self, artifact) -> dict[str, Any]:
        decrypted = self._codec.decrypt(artifact.payload_ciphertext)
        payload_value = json.loads(decrypted or "{}")
        canonical = json.dumps(payload_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != artifact.payload_digest:
            raise VoiceGovernanceError(
                code="voice_result.integrity_failed",
                message="voice result artifact integrity verification failed",
                status_code=500,
            )
        if not isinstance(payload_value, dict):
            raise VoiceGovernanceError(
                code="voice_result.invalid_payload",
                message="voice result artifact payload must be a JSON object",
                status_code=500,
            )
        return dict(payload_value)


_FORBIDDEN_AUDIO_KEYS = frozenset(
    {
        "audio",
        "audio_bytes",
        "audio_content",
        "audio_data",
        "audio_excerpt",
        "audio_payload",
        "audio_recording",
        "pcm",
        "pcm_bytes",
        "pcm_data",
        "raw_audio",
        "raw_audio_bytes",
        "recording",
        "recording_bytes",
        "wave_bytes",
        "waveform",
    }
)
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_KEY_CHARACTER_RE = re.compile(r"[^a-z0-9]+")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_AUDIO_MAGICS = (
    b"RIFF",
    b"OggS",
    b"fLaC",
    b"ID3",
    b"\xff\xfb",
    b"\xff\xf3",
    b"\xff\xf2",
)
_ENCODED_AUDIO_PREFIXES = ("UklGR", "T2dnUw", "ZkxhQw", "SUQz")
_MAX_RESULT_NODES = 50_000


def _normalized_result_key(raw_key: object) -> str:
    camel_split = _CAMEL_CASE_BOUNDARY_RE.sub("_", str(raw_key).strip())
    return _NON_KEY_CHARACTER_RE.sub("_", camel_split.casefold()).strip("_")


def _is_forbidden_audio_key(key: str) -> bool:
    if key in _FORBIDDEN_AUDIO_KEYS:
        return True
    return key.startswith(("raw_audio_", "audio_payload_", "audio_content_", "audio_data_", "pcm_")) or key.endswith(
        ("_audio_bytes", "_audio_payload", "_audio_content", "_audio_data", "_pcm_bytes", "_waveform")
    )


def _looks_like_encoded_binary(value: str) -> bool:
    compact = "".join(value.split())
    decoded: bytes | None = None
    if len(compact) >= 256 and len(compact) % 4 == 0 and _BASE64_RE.fullmatch(compact):
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            decoded = None
    elif len(compact) >= 512 and len(compact) % 2 == 0 and _HEX_RE.fullmatch(compact):
        try:
            decoded = bytes.fromhex(compact)
        except ValueError:
            decoded = None
    if not decoded:
        return False
    if decoded.startswith(_AUDIO_MAGICS):
        return True
    non_text = sum(byte == 0 or byte < 9 or 13 < byte < 32 or byte > 126 for byte in decoded)
    return non_text / len(decoded) >= 0.20


def _assert_no_raw_audio(
    value: object,
    *,
    depth: int = 0,
    node_budget: list[int] | None = None,
) -> None:
    budget = node_budget if node_budget is not None else [0]
    budget[0] += 1
    if budget[0] > _MAX_RESULT_NODES:
        raise VoiceGovernanceError(
            code="voice_result.too_complex",
            message="voice result artifact exceeds its structural budget",
            status_code=422,
        )
    if depth > 16:
        raise VoiceGovernanceError(
            code="voice_result.invalid_depth",
            message="voice result artifact nesting exceeds its limit",
            status_code=422,
        )
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _normalized_result_key(raw_key)
            if _is_forbidden_audio_key(key):
                raise VoiceGovernanceError(
                    code="voice_result.raw_audio_forbidden",
                    message="voice result artifacts cannot contain raw audio",
                    status_code=422,
                )
            _assert_no_raw_audio(child, depth=depth + 1, node_budget=budget)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_raw_audio(child, depth=depth + 1, node_budget=budget)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise VoiceGovernanceError(
            code="voice_result.raw_audio_forbidden",
            message="voice result artifacts cannot contain binary payloads",
            status_code=422,
        )
    if isinstance(value, str):
        normalized = value.lstrip().casefold()
        if (
            normalized.startswith("data:audio/")
            or (len(value) > 256 and value.startswith(_ENCODED_AUDIO_PREFIXES))
            or _looks_like_encoded_binary(value)
        ):
            raise VoiceGovernanceError(
                code="voice_result.raw_audio_forbidden",
                message="voice result artifacts cannot contain encoded audio",
                status_code=422,
            )


def _transcript_retention_seconds(principal: VoicePrincipal, profile_id: str) -> int:
    from agent.services.voice_consent_service import get_voice_consent_service

    consent = get_voice_consent_service().get(principal, profile_id)
    retention_policy = consent.get("retention_policy")
    policy = retention_policy if isinstance(retention_policy, Mapping) else {}
    transcript_value = policy.get("transcript_result")
    transcript = transcript_value if isinstance(transcript_value, Mapping) else {}
    return int(transcript.get("retention_days") or 1) * 86_400


voice_result_artifact_service = VoiceResultArtifactService()


def get_voice_result_artifact_service() -> VoiceResultArtifactService:
    return voice_result_artifact_service
