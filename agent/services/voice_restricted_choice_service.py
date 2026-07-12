"""Hub-owned strict-choice correction over known voice candidate IDs.

The voice runtime only produces candidates.  This service is the sole bridge
from a Hub voice result to restricted inference and can select only one of the
candidate IDs already present in that result.  Any ambiguity or failure returns
the original result object unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    get_path_ai_mode_policy_service,
)
from agent.services.restricted_inference_config_service import (
    TASK_CHOICE_SCORE,
    RestrictedInferenceConfigService,
)
from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceStatus,
)
from agent.services.restricted_inference_port import (
    ContractRestrictedInferencePort,
    HttpRestrictedInferenceTransport,
    HubTaskQueueRestrictedInferencePort,
    RestrictedInferencePort,
)

_CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_VOICE_TRANSCRIPT_PATH = "__voice__/transcript"


@dataclass(frozen=True)
class VoiceRestrictedChoiceOutcome:
    result: Mapping[str, Any]
    applied: bool
    reason_code: str
    selected_candidate_id: str = ""
    manifest_digest: str = ""


class VoiceRestrictedChoiceService:
    """Select a known transcript candidate without accepting generated text."""

    def __init__(
        self,
        *,
        inference_port: RestrictedInferencePort | None,
        manifest_resolver: Callable[[], str],
        manifest_engine_resolver: Callable[[], str] | None = None,
        device_resolver: Callable[[], str] | None = None,
        policy_service: PathAiModePolicyService | None = None,
        max_candidates: int = 16,
        max_candidate_chars: int = 4_000,
        max_prompt_chars: int = 48_000,
    ) -> None:
        if not 1 <= max_candidates <= 64:
            raise ValueError("max_candidates must be between 1 and 64")
        if max_candidate_chars < 1 or max_prompt_chars < max_candidate_chars:
            raise ValueError("voice restricted-choice character limits are invalid")
        self._port = inference_port
        self._manifest_resolver = manifest_resolver
        self._manifest_engine_resolver = manifest_engine_resolver or (lambda: "")
        self._device_resolver = device_resolver or (lambda: "cpu")
        self._policy = policy_service or get_path_ai_mode_policy_service()
        self._max_candidates = max_candidates
        self._max_candidate_chars = max_candidate_chars
        self._max_prompt_chars = max_prompt_chars

    def apply(
        self,
        base_result: Mapping[str, Any],
        *,
        effective_configuration: Mapping[str, Any] | None,
        tenant_id: str,
        task_id: str,
        run_id: str,
        request_id: str,
        deadline_epoch_ms: int,
        policy_hash: str,
    ) -> VoiceRestrictedChoiceOutcome:
        """Return ``base_result`` by identity on every non-success path."""

        configuration = dict(effective_configuration or {})
        flags = configuration.get("feature_flags")
        enabled = (
            configuration.get("correction_policy") == "restricted_choice"
            and isinstance(flags, Mapping)
            and flags.get("restricted_worker") is True
        )
        if not enabled:
            return self._unchanged(base_result, "restricted_choice_disabled")
        if self._port is None:
            return self._unchanged(base_result, "restricted_worker_unavailable")
        candidates = self._candidates(base_result)
        if len(candidates) < 2:
            return self._unchanged(base_result, "insufficient_candidates")
        try:
            manifest_id = str(self._manifest_resolver() or "").strip()
            if not manifest_id:
                return self._unchanged(base_result, "manifest_unavailable")
            prompt = self._prompt(candidates)
            choices = [candidate_id for candidate_id, _text in candidates]
            path_policy = self._policy.resolve(_VOICE_TRANSCRIPT_PATH)
            if not path_policy.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER):
                return self._unchanged(base_result, "restricted_choice_policy_blocked")
            if not path_policy.allow_logits:
                return self._unchanged(base_result, "restricted_choice_logits_blocked")
            manifest_engine = str(self._manifest_engine_resolver() or "").strip()
            if path_policy.allowed_model_engines and manifest_engine not in path_policy.allowed_model_engines:
                return self._unchanged(base_result, "restricted_choice_engine_blocked")
            max_batch_size = self._max_candidates
            if path_policy.max_batch_size > 0:
                max_batch_size = min(max_batch_size, path_policy.max_batch_size)
            if len(choices) > max_batch_size:
                return self._unchanged(base_result, "restricted_choice_batch_limit")
            max_input_chars = min(16_000_000, self._max_prompt_chars * 2)
            if path_policy.max_input_chars > 0:
                max_input_chars = min(max_input_chars, path_policy.max_input_chars)
            if len(prompt) > max_input_chars:
                return self._unchanged(base_result, "restricted_choice_input_limit")
            execution_policy = {
                "allow_attention": False,
                "allow_hidden_states": False,
                "allow_cpu_fallback": False,
                "device": str(self._device_resolver() or "cpu"),
                "max_batch_size": max_batch_size,
                "max_candidates": max_batch_size,
                "max_input_chars": max_input_chars,
                "max_output_dimensions": 1,
            }
            effective_policy_hash = hashlib.sha256(
                json.dumps(
                    {
                        "base_policy_hash": policy_hash,
                        "execution_policy": execution_policy,
                        "path_policy": path_policy.to_dict(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            request = RestrictedInferenceRequest(
                request_id=_wire_identifier("voice-request", request_id),
                task_id=_wire_identifier("voice-task", task_id),
                run_id=_wire_identifier("voice-run", run_id),
                tenant_id=_wire_identifier("voice-tenant", tenant_id),
                operation=RestrictedInferenceOperation.SCORE_CHOICES,
                payload={"prompt": prompt, "choices": choices},
                model_manifest_id=manifest_id,
                policy_hash=effective_policy_hash,
                deadline_epoch_ms=deadline_epoch_ms,
                paths=(_VOICE_TRANSCRIPT_PATH,),
                idempotency_key=f"voice-choice-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:32]}",
                execution_policy=execution_policy,
            )
            response = self._port.execute(request)
        except Exception:
            return self._unchanged(base_result, "restricted_choice_failed")
        if response.status is not RestrictedInferenceStatus.SUCCEEDED or response.result is None:
            return self._unchanged(base_result, "restricted_choice_failed")
        items = response.result.get("items")
        if not isinstance(items, tuple) or len(items) != len(candidates):
            return self._unchanged(base_result, "invalid_choice_result")
        try:
            scores = {str(item.get("choice") or ""): float(item.get("score")) for item in items}
        except (TypeError, ValueError):
            return self._unchanged(base_result, "invalid_choice_result")
        if any(not math.isfinite(score) for score in scores.values()):
            return self._unchanged(base_result, "invalid_choice_result")
        if set(scores) != set(choices):
            return self._unchanged(base_result, "invalid_choice_result")
        ordered = sorted(scores.items(), key=lambda item: (-item[1], choices.index(item[0])))
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            return self._unchanged(base_result, "ambiguous_choice_result")
        selected_id = ordered[0][0]
        selected_text = dict(candidates)[selected_id]
        if selected_id == str(base_result.get("selected_candidate_id") or "") and selected_text == str(
            base_result.get("text") or ""
        ):
            return self._unchanged(base_result, "base_candidate_confirmed")
        updated = dict(base_result)
        updated["text"] = selected_text
        updated["selected_candidate_id"] = selected_id
        trace = dict(updated.get("decision_trace") or {})
        trace["restricted_choice"] = {
            "applied": True,
            "selected_candidate_id": selected_id,
            "candidate_ids": choices,
            "no_generation": True,
            "manifest_digest": str(response.result["manifest_digest"]),
        }
        updated["decision_trace"] = trace
        warnings = list(updated.get("warnings") or [])
        warnings.append("hub_restricted_choice_applied")
        updated["warnings"] = warnings
        return VoiceRestrictedChoiceOutcome(
            result=updated,
            applied=True,
            reason_code="restricted_choice_applied",
            selected_candidate_id=selected_id,
            manifest_digest=str(response.result["manifest_digest"]),
        )

    def _candidates(self, base_result: Mapping[str, Any]) -> list[tuple[str, str]]:
        raw_candidates = base_result.get("candidates")
        if not isinstance(raw_candidates, list) or len(raw_candidates) > self._max_candidates:
            return []
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in raw_candidates:
            if not isinstance(raw, Mapping):
                return []
            candidate_id = str(raw.get("candidate_id") or "").strip()
            text = str(raw.get("text") or "")
            status = str(raw.get("status") or "succeeded")
            if status != "succeeded":
                continue
            if (
                not _CANDIDATE_ID_RE.fullmatch(candidate_id)
                or candidate_id in seen
                or not text.strip()
                or len(text) > self._max_candidate_chars
            ):
                return []
            seen.add(candidate_id)
            candidates.append((candidate_id, text))
        return candidates

    def _prompt(self, candidates: list[tuple[str, str]]) -> str:
        payload = {
            "task": "select_exact_voice_candidate_id",
            "constraint": "return_scores_for_known_ids_only_no_generation",
            "candidates": [{"candidate_id": candidate_id, "text": text} for candidate_id, text in candidates],
        }
        prompt = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(prompt) > self._max_prompt_chars:
            raise ValueError("voice restricted-choice prompt exceeds limit")
        return prompt

    @staticmethod
    def _unchanged(base_result: Mapping[str, Any], reason_code: str) -> VoiceRestrictedChoiceOutcome:
        return VoiceRestrictedChoiceOutcome(result=base_result, applied=False, reason_code=reason_code)


def _environment_port() -> RestrictedInferencePort | None:
    endpoint = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_URL", "")).strip()
    allowed_endpoints = tuple(
        item.strip()
        for item in str(os.getenv("ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS", "")).split(",")
        if item.strip()
    )
    token = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", "")).strip()
    if not endpoint or not token or not allowed_endpoints:
        return None
    worker_port = ContractRestrictedInferencePort(
        HttpRestrictedInferenceTransport(
            endpoint=endpoint,
            allowed_endpoints=allowed_endpoints,
            bearer_token=token,
        )
    )
    return HubTaskQueueRestrictedInferencePort(worker_port)


def _manifest_from_environment() -> str:
    configured = str(
        os.getenv("ANANTA_VOICE_RESTRICTED_CHOICE_MANIFEST_ID")
        or os.getenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES")
        or os.getenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ID")
        or ""
    ).strip()
    if configured:
        return configured
    model = _configured_choice_model()
    return str(model.id if model is not None else "")


def _manifest_engine_from_environment() -> str:
    configured = str(
        os.getenv("ANANTA_RESTRICTED_INFERENCE_ENGINE_SCORE_CHOICES")
        or os.getenv("ANANTA_RESTRICTED_INFERENCE_ENGINE")
        or ""
    ).strip()
    if configured:
        return configured
    model = _configured_choice_model()
    return str(model.engine if model is not None else "")


def _device_from_environment() -> str:
    configured = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_DEVICE") or "").strip()
    if configured:
        return configured
    model = _configured_choice_model()
    return str(model.device if model is not None else "cpu")


def _configured_choice_model():
    from agent.services.user_config_service import get_user_config_service

    config = RestrictedInferenceConfigService(global_config=get_user_config_service().config).resolve()
    return config.model_for_task(TASK_CHOICE_SCORE)


_service: VoiceRestrictedChoiceService | None = None


def get_voice_restricted_choice_service() -> VoiceRestrictedChoiceService:
    global _service
    if _service is None:
        _service = VoiceRestrictedChoiceService(
            inference_port=_environment_port(),
            manifest_resolver=_manifest_from_environment,
            manifest_engine_resolver=_manifest_engine_from_environment,
            device_resolver=_device_from_environment,
        )
    return _service


def reset_voice_restricted_choice_service(service: VoiceRestrictedChoiceService | None = None) -> None:
    global _service
    _service = service


def voice_choice_policy_hash(configuration: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(configuration), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def voice_choice_deadline_epoch_ms(deadline_seconds: float | None) -> int:
    bounded = max(0.1, min(float(deadline_seconds or 30.0), 30.0))
    return time.time_ns() // 1_000_000 + int(bounded * 1000)


def new_voice_choice_run_id() -> str:
    return f"voice-run-{uuid.uuid4().hex}"


def _wire_identifier(prefix: str, value: str) -> str:
    normalized = str(value or "").strip()
    if _CANDIDATE_ID_RE.fullmatch(normalized):
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"
