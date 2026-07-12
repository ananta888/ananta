"""Canonical Hub-owned voice/fusion configuration resolution."""

from __future__ import annotations

import hashlib
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from agent.repositories.voice_configuration import VoiceConfigurationRepository
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal, validate_identifier
from agent.services.voice_idempotency_service import VoiceIdempotencyService

SCHEMA_VERSION = "ananta.voice-configuration.v1"
_CONFIGURATION_MUTATION_LOCKS = tuple(threading.RLock() for _index in range(64))

STRATEGY_ENUMS = {
    "transport_mode": ("batch", "streaming"),
    "recognition_strategy": ("single", "parallel_compare", "classic_then_correct"),
    "routing_strategy": ("fixed", "fallback", "adaptive"),
    "correction_policy": ("none", "deterministic", "restricted_choice", "generative_local"),
    "review_policy": ("automatic", "on_disagreement", "always"),
}
BACKENDS = ("vosk", "whisper_cpp", "faster_whisper", "voxtral")
FEATURE_FLAGS = (
    "voice_fusion",
    "audio_enhancement",
    "adaptive_routing",
    "restricted_worker",
    "codecompass_reranking",
    "personalization",
    "optional_models",
    "generative_judge",
)
LEGACY_FEATURE_FLAG_ALIASES = {
    "voice_fusion_enabled": "voice_fusion",
    "audio_enhancement_enabled": "audio_enhancement",
    "adaptive_routing_enabled": "adaptive_routing",
    "restricted_worker_enabled": "restricted_worker",
    "codecompass_reranking_enabled": "codecompass_reranking",
    "personalization_enabled": "personalization",
    "optional_models_enabled": "optional_models",
    "generative_judge_enabled": "generative_judge",
}
LEGACY_PIPELINE_PROJECTION = {
    "simple": {"transport_mode": "batch", "recognition_strategy": "single"},
    "oldschool_light": {"transport_mode": "batch", "recognition_strategy": "single"},
    "whisper_cpp": {
        "transport_mode": "batch",
        "recognition_strategy": "single",
        "primary_backend": "whisper_cpp",
    },
    "realtime_streaming": {"transport_mode": "streaming", "recognition_strategy": "single"},
    "meeting": {"transport_mode": "batch", "recognition_strategy": "single"},
    "confidence_rerun": {"transport_mode": "batch", "recognition_strategy": "single"},
    "custom": {"transport_mode": "batch", "recognition_strategy": "single"},
}
ENHANCEMENT_VARIANTS = ("original", "bypass", "normalized", "high_pass", "speech_safe")
DIARIZATION_BACKENDS = ("none", "pyannote")

DEFAULT_CONFIGURATION: dict[str, Any] = {
    "transport_mode": "batch",
    "recognition_strategy": "single",
    "routing_strategy": "fallback",
    "correction_policy": "deterministic",
    "review_policy": "on_disagreement",
    "primary_backend": "vosk",
    "secondary_backends": ["whisper_cpp"],
    "max_parallel_backends": 2,
    "candidate_deadline_sec": 120.0,
    "confidence_threshold": 0.7,
    "enhancement_variants": ["original"],
    "diarization_backend": "none",
    "feature_flags": {name: False for name in FEATURE_FLAGS},
}


@dataclass(frozen=True)
class EffectiveVoiceConfiguration:
    effective: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    version: str
    adjustments: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "effective": deepcopy(self.effective),
            "sources": [dict(item) for item in self.sources],
            "version": self.version,
            "adjustments": [dict(item) for item in self.adjustments],
        }


class VoiceConfigurationService:
    def __init__(
        self,
        repository: VoiceConfigurationRepository | None = None,
        idempotency: VoiceIdempotencyService | None = None,
    ) -> None:
        self._repository = repository or VoiceConfigurationRepository()
        self._idempotency = idempotency or VoiceIdempotencyService()

    @staticmethod
    def schema() -> dict[str, Any]:
        common_scope = {
            "scopes": ["global", "profile", "session"],
            "visibility": "standard",
            "secret_reference": False,
        }
        properties: dict[str, Any] = {
            key: {
                "type": "string",
                "enum": list(values),
                "default": DEFAULT_CONFIGURATION[key],
                **common_scope,
            }
            for key, values in STRATEGY_ENUMS.items()
        }
        properties.update(
            {
                "primary_backend": {
                    "type": "string",
                    "enum": list(BACKENDS),
                    "default": DEFAULT_CONFIGURATION["primary_backend"],
                    "capability_reason_source": "/v1/voice/capabilities/model_catalog",
                    **common_scope,
                },
                "secondary_backends": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(BACKENDS)},
                    "uniqueItems": True,
                    "maxItems": 3,
                    "default": list(DEFAULT_CONFIGURATION["secondary_backends"]),
                    "capability_reason_source": "/v1/voice/capabilities/model_catalog",
                    **common_scope,
                },
                "max_parallel_backends": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "default": DEFAULT_CONFIGURATION["max_parallel_backends"],
                    **common_scope,
                },
                "candidate_deadline_sec": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 300,
                    "default": DEFAULT_CONFIGURATION["candidate_deadline_sec"],
                    **common_scope,
                },
                "confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": DEFAULT_CONFIGURATION["confidence_threshold"],
                    **common_scope,
                },
                "enhancement_variants": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(ENHANCEMENT_VARIANTS)},
                    "uniqueItems": True,
                    "minItems": 1,
                    "maxItems": 4,
                    "default": list(DEFAULT_CONFIGURATION["enhancement_variants"]),
                    "required_capabilities": ["voice_fusion", "audio_enhancement"],
                    **common_scope,
                },
                "diarization_backend": {
                    "type": "string",
                    "enum": list(DIARIZATION_BACKENDS),
                    "default": DEFAULT_CONFIGURATION["diarization_backend"],
                    "required_capabilities": ["diarization"],
                    "capability_reason_source": "/v1/voice/capabilities/model_catalog",
                    **common_scope,
                },
                "feature_flags": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        name: {
                            "type": "boolean",
                            "default": False,
                            "scopes": ["global", "profile", "session"],
                            "visibility": "advanced",
                            "secret_reference": False,
                        }
                        for name in FEATURE_FLAGS
                    },
                    **common_scope,
                },
            }
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "precedence": ["defaults", "legacy_global", "global_delta", "profile_delta", "session_delta"],
            "administrative_fields": {
                "scope": "global_admin_only",
                "fields": ["model_paths", "device_allocation", "download_policy", "service_tokens"],
                "session_override_allowed": False,
                "secret_reference_required": True,
            },
        }

    def resolve(
        self,
        principal: VoicePrincipal,
        *,
        legacy_global: Mapping[str, Any] | None = None,
        profile_id: str | None = None,
        session_id: str | None = None,
    ) -> EffectiveVoiceConfiguration:
        effective = deepcopy(DEFAULT_CONFIGURATION)
        sources: list[dict[str, Any]] = [{"scope": "defaults", "scope_id": "", "version": 1}]
        legacy_delta = self._legacy_delta(legacy_global or {})
        if legacy_delta:
            self._merge(effective, legacy_delta)
            sources.append({"scope": "legacy_global", "scope_id": "", "version": 1})
        scopes = [("global", "")]
        if profile_id:
            scopes.append(("profile", validate_identifier(profile_id, field="profile_id")))
        if session_id:
            scopes.append(("session", validate_identifier(session_id, field="session_id")))
        version_parts: list[str] = ["defaults:1"]
        for scope, scope_id in scopes:
            record = self._repository.get(principal, scope=scope, scope_id=scope_id)
            if record is None:
                continue
            self._merge(effective, self.normalize_delta(record.delta))
            sources.append(
                {
                    "scope": scope,
                    "scope_id": scope_id,
                    "version": record.version,
                    "delta": deepcopy(record.delta),
                }
            )
            version_parts.append(f"{scope}:{scope_id}:{record.version}")
        self._validate_effective(effective)
        adjustments = self._apply_safe_feature_flags(effective)
        return EffectiveVoiceConfiguration(effective, tuple(sources), "|".join(version_parts), adjustments)

    def put_delta(
        self,
        principal: VoicePrincipal,
        *,
        scope: str,
        scope_id: str | None,
        delta: Mapping[str, Any],
        expected_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if scope not in {"global", "profile", "session"}:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_scope",
                message="scope must be global, profile, or session",
                status_code=422,
            )
        normalized_scope_id = "" if scope == "global" else validate_identifier(scope_id, field="scope_id")
        normalized_delta = self.normalize_delta(delta)
        # Repository row locking and lease fencing remain authoritative across
        # Hub processes. This striped lock also gives deterministic singleflight
        # behavior to local retries and SQLite-backed test/development Hubs.
        lock_owner = "__tenant_global__" if scope == "global" else principal.subject
        lock_scope = f"{principal.tenant_id}\0{lock_owner}\0{scope}\0{normalized_scope_id}"
        lock_index = int(hashlib.sha256(lock_scope.encode("utf-8")).hexdigest()[:8], 16)
        with _CONFIGURATION_MUTATION_LOCKS[lock_index % len(_CONFIGURATION_MUTATION_LOCKS)]:
            return self._put_delta_locked(
                principal,
                scope=scope,
                normalized_scope_id=normalized_scope_id,
                normalized_delta=normalized_delta,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )

    def _put_delta_locked(
        self,
        principal: VoicePrincipal,
        *,
        scope: str,
        normalized_scope_id: str,
        normalized_delta: dict[str, Any],
        expected_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        baseline = deepcopy(DEFAULT_CONFIGURATION)
        if scope != "global":
            baseline = self.resolve(principal).effective
        self._merge(baseline, normalized_delta)
        self._validate_effective(baseline)
        operation = f"voice_configuration.put:{scope}:{normalized_scope_id}"
        payload = {
            "scope": scope,
            "scope_id": normalized_scope_id,
            "delta": normalized_delta,
            "expected_version": expected_version,
        }
        claim = self._idempotency.begin(
            principal,
            operation=operation,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            return {**claim.result_metadata, "idempotent_replay": True}
        if claim.lease_token is None:
            raise RuntimeError("active configuration idempotency claim has no lease token")
        try:
            _record, result = self._repository.put(
                principal,
                scope=scope,
                scope_id=normalized_scope_id,
                delta=normalized_delta,
                expected_version=expected_version,
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=claim.lease_token,
                result_builder=lambda record: {
                    "schema_version": SCHEMA_VERSION,
                    "scope": scope,
                    "scope_id": normalized_scope_id,
                    "delta": dict(record.delta),
                    "version": record.version,
                    "idempotent_replay": False,
                },
            )
            return dict(result)
        except Exception:
            self._idempotency.abandon(claim)
            raise

    @staticmethod
    def normalize_delta(raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_delta",
                message="delta must be an object",
                status_code=422,
            )
        allowed_fields = {
            *STRATEGY_ENUMS,
            "primary_backend",
            "secondary_backends",
            "max_parallel_backends",
            "candidate_deadline_sec",
            "confidence_threshold",
            "enhancement_variants",
            "diarization_backend",
            "feature_flags",
        }
        unknown = set(raw) - allowed_fields
        if unknown:
            raise VoiceGovernanceError(
                code="voice_configuration.unknown_field",
                message=f"unknown voice configuration fields: {sorted(unknown)}",
                status_code=422,
            )
        result: dict[str, Any] = {}
        for key, allowed in STRATEGY_ENUMS.items():
            if key not in raw:
                continue
            value = str(raw[key] or "").strip()
            if value not in allowed:
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_value",
                    message=f"invalid {key}",
                    status_code=422,
                )
            result[key] = value
        if "primary_backend" in raw:
            result["primary_backend"] = VoiceConfigurationService._backend(raw["primary_backend"])
        if "secondary_backends" in raw:
            values = raw["secondary_backends"]
            if not isinstance(values, list) or len(values) > 3:
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_backends",
                    message="secondary_backends must be an array with at most three entries",
                    status_code=422,
                )
            normalized_backends = (VoiceConfigurationService._backend(item) for item in values)
            result["secondary_backends"] = list(dict.fromkeys(normalized_backends))
        if "enhancement_variants" in raw:
            values = raw["enhancement_variants"]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 4
                or any(not isinstance(item, str) or item not in ENHANCEMENT_VARIANTS for item in values)
                or len(set(values)) != len(values)
                or values[0] != "original"
            ):
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_enhancement_variants",
                    message="enhancement_variants must start with original and contain unique supported values",
                    status_code=422,
                )
            result["enhancement_variants"] = list(values)
        if "diarization_backend" in raw:
            value = str(raw["diarization_backend"] or "").strip()
            if value not in DIARIZATION_BACKENDS:
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_diarization_backend",
                    message="unsupported diarization backend",
                    status_code=422,
                )
            result["diarization_backend"] = value
        numeric_limits = {
            "max_parallel_backends": (int, 1, 4),
            "candidate_deadline_sec": (float, 1, 300),
            "confidence_threshold": (float, 0, 1),
        }
        for key, (converter, minimum, maximum) in numeric_limits.items():
            if key not in raw:
                continue
            try:
                value = converter(raw[key])
            except (TypeError, ValueError) as exc:
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_value", message=f"invalid {key}", status_code=422
                ) from exc
            if value < minimum or value > maximum:
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_value", message=f"invalid {key}", status_code=422
                )
            result[key] = value
        if "feature_flags" in raw:
            flags = raw["feature_flags"]
            if not isinstance(flags, Mapping) or set(flags) - set(FEATURE_FLAGS):
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_feature_flags",
                    message="feature_flags contains unknown fields",
                    status_code=422,
                )
            if any(not isinstance(value, bool) for value in flags.values()):
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_feature_flags",
                    message="feature flags must be boolean",
                    status_code=422,
                )
            result["feature_flags"] = dict(flags)
        if result.get("primary_backend") in result.get("secondary_backends", []):
            raise VoiceGovernanceError(
                code="voice_configuration.duplicate_backend",
                message="primary backend cannot also be a secondary backend",
                status_code=422,
            )
        return result

    @staticmethod
    def _backend(value: Any) -> str:
        normalized = str(value or "").strip()
        if normalized not in BACKENDS:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_backend",
                message="unsupported voice backend",
                status_code=422,
            )
        return normalized

    @staticmethod
    def _validate_effective(effective: Mapping[str, Any]) -> None:
        primary = str(effective.get("primary_backend") or "")
        secondary_value = effective.get("secondary_backends")
        secondary = list(secondary_value) if isinstance(secondary_value, list) else []
        if primary in secondary:
            raise VoiceGovernanceError(
                code="voice_configuration.duplicate_backend",
                message="effective primary backend cannot also be a secondary backend",
                status_code=422,
            )

    @staticmethod
    def _merge(target: dict[str, Any], delta: Mapping[str, Any]) -> None:
        for key, value in delta.items():
            if key == "feature_flags":
                target["feature_flags"].update(dict(value))
            else:
                target[key] = deepcopy(value)

    @staticmethod
    def _apply_safe_feature_flags(effective: dict[str, Any]) -> tuple[dict[str, str], ...]:
        """Apply compatibility fallbacks without mutating persisted sparse deltas."""

        flags_value = effective.get("feature_flags")
        flags: dict[str, Any] = flags_value if isinstance(flags_value, dict) else {}
        adjustments: list[dict[str, str]] = []
        if effective.get("recognition_strategy") == "parallel_compare" and not flags.get("voice_fusion", False):
            effective["recognition_strategy"] = "single"
            adjustments.append(
                {
                    "field": "recognition_strategy",
                    "requested": "parallel_compare",
                    "effective": "single",
                    "reason_code": "voice_fusion_disabled",
                }
            )
        enhancement_value = effective.get("enhancement_variants")
        if not isinstance(enhancement_value, list):
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_enhancement_variants",
                message="effective enhancement_variants must be an array",
                status_code=422,
            )
        if len(enhancement_value) > 1 and not flags.get("audio_enhancement", False):
            requested_variants = list(enhancement_value)
            effective["enhancement_variants"] = ["original"]
            adjustments.append(
                {
                    "field": "enhancement_variants",
                    "requested": ",".join(requested_variants),
                    "effective": "original",
                    "reason_code": "audio_enhancement_disabled",
                }
            )
        if effective.get("diarization_backend") == "pyannote" and not flags.get("optional_models", False):
            effective["diarization_backend"] = "none"
            adjustments.append(
                {
                    "field": "diarization_backend",
                    "requested": "pyannote",
                    "effective": "none",
                    "reason_code": "optional_models_disabled",
                }
            )
        correction = str(effective.get("correction_policy") or "none")
        if correction == "restricted_choice" and not flags.get("restricted_worker", False):
            effective["correction_policy"] = "deterministic"
            adjustments.append(
                {
                    "field": "correction_policy",
                    "requested": correction,
                    "effective": "deterministic",
                    "reason_code": "restricted_worker_disabled",
                }
            )
        elif correction == "generative_local" and not flags.get("generative_judge", False):
            effective["correction_policy"] = "deterministic"
            adjustments.append(
                {
                    "field": "correction_policy",
                    "requested": correction,
                    "effective": "deterministic",
                    "reason_code": "generative_judge_disabled",
                }
            )
        return tuple(adjustments)

    @staticmethod
    def _legacy_delta(raw: Mapping[str, Any]) -> dict[str, Any]:
        voice_value = raw.get("voice_runtime")
        voice: Mapping[str, Any] = voice_value if isinstance(voice_value, Mapping) else raw
        candidate = VoiceConfigurationService._legacy_pipeline_projection(
            voice.get("transcription_pipeline")
        )

        explicit_primary = "primary_backend" in voice
        explicit_secondary = "secondary_backends" in voice
        fallback_backends = VoiceConfigurationService._legacy_fallback_backends(
            voice.get("backend_fallback_order")
        )
        legacy_asr = str(voice.get("asr_backend") or "").strip()
        if legacy_asr and legacy_asr not in {*BACKENDS, "mock"}:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_legacy_backend",
                message="legacy asr_backend contains an unsupported voice backend",
                status_code=422,
            )
        if not explicit_primary:
            if fallback_backends:
                candidate["primary_backend"] = fallback_backends[0]
            elif legacy_asr in BACKENDS:
                candidate["primary_backend"] = legacy_asr
        if not explicit_secondary and fallback_backends:
            effective_primary = str(voice.get("primary_backend") or candidate.get("primary_backend") or "")
            candidate["secondary_backends"] = [
                backend for backend in fallback_backends if backend != effective_primary
            ][:3]

        legacy_flags: dict[str, bool] = {}
        for old_name, canonical_name in LEGACY_FEATURE_FLAG_ALIASES.items():
            if old_name not in voice:
                continue
            value = voice[old_name]
            if not isinstance(value, bool):
                raise VoiceGovernanceError(
                    code="voice_configuration.invalid_legacy_feature_flag",
                    message=f"legacy feature flag {old_name} must be boolean",
                    status_code=422,
                )
            legacy_flags[canonical_name] = value
        explicit_flags = voice.get("feature_flags")
        if explicit_flags is not None and not isinstance(explicit_flags, Mapping):
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_feature_flags",
                message="feature_flags must be an object",
                status_code=422,
            )
        merged_flags = {**legacy_flags, **dict(explicit_flags or {})}
        if merged_flags:
            candidate["feature_flags"] = merged_flags

        aliases = {
            "transport_mode": "transport_mode",
            "recognition_strategy": "recognition_strategy",
            "routing_strategy": "routing_strategy",
            "correction_policy": "correction_policy",
            "review_policy": "review_policy",
            "primary_backend": "primary_backend",
            "secondary_backends": "secondary_backends",
            "max_parallel_backends": "max_parallel_backends",
            "candidate_deadline_sec": "candidate_deadline_sec",
            "confidence_threshold": "confidence_threshold",
            "enhancement_variants": "enhancement_variants",
            "diarization_backend": "diarization_backend",
        }
        candidate.update({target: voice[source] for source, target in aliases.items() if source in voice})
        return VoiceConfigurationService.normalize_delta(candidate) if candidate else {}

    @staticmethod
    def _legacy_pipeline_projection(value: Any) -> dict[str, Any]:
        if value is None or str(value).strip() == "":
            return {}
        normalized = str(value).strip().lower()
        projection = LEGACY_PIPELINE_PROJECTION.get(normalized)
        if projection is None:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_legacy_pipeline",
                message="legacy transcription_pipeline is unsupported",
                status_code=422,
            )
        return deepcopy(projection)

    @staticmethod
    def _legacy_fallback_backends(value: Any) -> list[str]:
        if value is None:
            return []
        raw_backends: list[Any]
        if isinstance(value, str):
            raw_backends = value.split(",")
        elif isinstance(value, (list, tuple)):
            raw_backends = list(value)
        else:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_legacy_backends",
                message="legacy backend_fallback_order must be an array or comma-separated string",
                status_code=422,
            )
        normalized = [str(item).strip() for item in raw_backends if str(item).strip()]
        unknown = sorted(set(normalized) - {*BACKENDS, "mock"})
        if unknown:
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_legacy_backend",
                message="legacy backend_fallback_order contains an unsupported voice backend",
                status_code=422,
            )
        # Mock was a development-only terminal fallback and has no legal
        # representation in the canonical production configuration.
        return list(dict.fromkeys(item for item in normalized if item in BACKENDS))


voice_configuration_service = VoiceConfigurationService()


def get_voice_configuration_service() -> VoiceConfigurationService:
    return voice_configuration_service
