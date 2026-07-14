"""Hub-owned delegation boundary for bounded generative transcript correction."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.config import settings
from agent.services.generative_corrector_worker_port import HttpGenerativeCorrectorWorkerPort
from ananta_contracts.voice_corrector_worker import (
    VoiceCorrectorWorkerPort,
    VoiceCorrectorWorkerRequest,
)

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_PROTECTED_TOKEN_RE = re.compile(r"https?://\S+|\b[\w.:-]*\d[\w.:-]*\b", re.UNICODE)


@dataclass(frozen=True)
class VoiceGenerativeCorrectorOutcome:
    result: dict[str, Any]
    applied: bool
    reason_code: str


class VoiceGenerativeCorrectorTaskTrackerPort(Protocol):
    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
        model_id: str,
    ) -> str: ...

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class VoiceGenerativeCorrectorTaskTracker:
    """Persist opaque correlation only; transcript content remains in result storage."""

    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
        model_id: str,
    ) -> str:
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.voice_task_scope import inherited_voice_task_scope

        correlation = f"{parent_task_id}\0{request_id}\0{content_digest}\0{model_id}".encode()
        task_id = f"voice-generative-corrector-{hashlib.sha256(correlation).hexdigest()[:32]}"
        inherited_scope = inherited_voice_task_scope(parent_task_id, tenant_id=tenant_id)
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title="Hub-delegated Voice transcript corrector",
            description="Execute one bounded rewrite in the isolated generative-corrector worker.",
            priority="low",
            created_by="hub",
            source="voice_api",
            tags=["voice_transcription", "generative_corrector", "worker_delegation"],
            event_type="voice_generative_corrector_delegated",
            event_details={"request_id": request_id, "model_id": model_id},
            extra_fields={
                "task_kind": "voice_generative_corrector",
                "parent_task_id": parent_task_id,
                "required_capabilities": ["voice_generative_corrector_worker"],
                "worker_execution_context": {
                    "voice_generative_corrector": {
                        "request_id": request_id,
                        "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
                        **inherited_scope,
                        "content_digest": content_digest,
                        "policy_digest": policy_digest,
                        "model_id": model_id,
                        "persistence_owner": "hub",
                    }
                },
            },
        )
        return task_id

    @staticmethod
    def finish(task_id: str, *, status: str, reason_code: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        succeeded = status in {"corrected", "unchanged"}
        update_local_task_status(
            task_id,
            "completed" if succeeded else "failed",
            status_reason_code=None if succeeded else reason_code,
            status_reason_details={} if succeeded else {"fallback": True},
            verification_status={
                "voice_generative_corrector": {
                    "status": "verified" if succeeded else "fallback",
                    "reason_code": reason_code,
                }
            },
            event_type=(
                "voice_generative_corrector_completed"
                if succeeded
                else "voice_generative_corrector_failed"
            ),
            event_actor="hub",
            event_details={"status": status, "reason_code": reason_code},
        )


class VoiceGenerativeCorrectorService:
    """Delegate a rewrite and fail open to the byte-exact ASR transcript."""

    def __init__(
        self,
        worker_port: VoiceCorrectorWorkerPort | None = None,
        task_tracker: VoiceGenerativeCorrectorTaskTrackerPort | None = None,
    ) -> None:
        self._worker_port = worker_port
        self._task_tracker = (
            task_tracker if task_tracker is not None else VoiceGenerativeCorrectorTaskTracker()
        )

    def apply(
        self,
        result: Mapping[str, Any],
        *,
        effective_configuration: Mapping[str, Any],
        tenant_id: str | None = None,
        parent_task_id: str | None = None,
        request_id: str | None = None,
        language: str | None = None,
        deadline_epoch_ms: int | None = None,
    ) -> VoiceGenerativeCorrectorOutcome:
        baseline = str(result.get("text") or "")
        flags = effective_configuration.get("feature_flags")
        enabled = (
            effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(flags, Mapping)
            and flags.get("generative_corrector") is True
        )
        if not enabled or not baseline:
            return self._fallback(result, "generative_corrector_disabled", baseline=baseline)
        if len(baseline) > 8_000 or "\x00" in baseline:
            return self._fallback(result, "generative_corrector_invalid_baseline", baseline=baseline)
        model_id = str(effective_configuration.get("generative_corrector_model") or "").strip()
        if not _MODEL_ID_RE.fullmatch(model_id) or model_id not in configured_corrector_models():
            return self._fallback(result, "generative_corrector_model_not_allowlisted", baseline=baseline)
        try:
            max_edit_ratio = float(
                effective_configuration.get("generative_corrector_max_edit_ratio", 0.35)
            )
        except (TypeError, ValueError):
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        if not math.isfinite(max_edit_ratio) or not 0.01 <= max_edit_ratio <= 1.0:
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        worker_port = self._worker_port if self._worker_port is not None else _configured_worker_port()
        if worker_port is None:
            return self._fallback(result, "generative_corrector_unavailable", baseline=baseline)
        if not tenant_id or not parent_task_id or not request_id:
            return self._fallback(result, "generative_corrector_correlation_missing", baseline=baseline)

        timeout_ms = max(1, min(int(settings.voice_generative_corrector_timeout_ms), 120_000))
        now_ms = time.time_ns() // 1_000_000
        local_deadline_ms = now_ms + timeout_ms
        if deadline_epoch_ms is not None:
            if isinstance(deadline_epoch_ms, bool) or not isinstance(deadline_epoch_ms, int):
                return self._fallback(result, "generative_corrector_deadline_expired", baseline=baseline)
            local_deadline_ms = min(local_deadline_ms, deadline_epoch_ms)
        if local_deadline_ms <= now_ms:
            return self._fallback(result, "generative_corrector_deadline_expired", baseline=baseline)

        try:
            policy_payload = json.dumps(
                dict(effective_configuration),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError):
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        task_id: str | None = None
        try:
            task_id = self._task_tracker.start(
                tenant_id=tenant_id,
                parent_task_id=parent_task_id,
                request_id=request_id,
                content_digest=hashlib.sha256(baseline.encode()).hexdigest(),
                policy_digest=hashlib.sha256(policy_payload).hexdigest(),
                model_id=model_id,
            )
            worker_request = VoiceCorrectorWorkerRequest(
                request_id=request_id,
                task_id=task_id,
                region_id="full-transcript",
                original_text=baseline,
                model_id=model_id,
                language=str(language).strip() if language else None,
                max_edit_ratio=max_edit_ratio,
                deadline_epoch_ms=local_deadline_ms,
            )
            worker_response = worker_port.execute(worker_request)
            worker_response.validate_for(worker_request)
        except Exception:
            if task_id is not None:
                self._finish_safely(task_id, status="failed", reason_code="generative_corrector_failed")
            return self._fallback(
                result,
                "generative_corrector_failed",
                baseline=baseline,
                task_id=task_id,
            )

        reason_code = worker_response.reason_code or f"generative_corrector_{worker_response.status}"
        if worker_response.status == "failed" or worker_response.corrected_text is None:
            try:
                self._task_tracker.finish(task_id, status="failed", reason_code=reason_code)
            except Exception:
                return self._fallback(
                    result,
                    "generative_corrector_tracking_failed",
                    baseline=baseline,
                    task_id=task_id,
                )
            return self._fallback(result, reason_code, baseline=baseline, task_id=task_id)
        corrected = worker_response.corrected_text
        if _protected_tokens(baseline) != _protected_tokens(corrected):
            protected_reason = "generative_corrector_protected_token_changed"
            self._finish_safely(task_id, status="failed", reason_code=protected_reason)
            return self._fallback(
                result,
                protected_reason,
                baseline=baseline,
                task_id=task_id,
            )
        try:
            self._task_tracker.finish(task_id, status=worker_response.status, reason_code=reason_code)
        except Exception:
            return self._fallback(
                result,
                "generative_corrector_tracking_failed",
                baseline=baseline,
                task_id=task_id,
            )

        updated = dict(result)
        updated["original_text"] = baseline
        updated["text"] = corrected
        updated["generative_corrector"] = {
            "schema_version": "ananta.voice-generative-correction.v1",
            "status": worker_response.status,
            "applied": corrected != baseline,
            "changed": corrected != baseline,
            "review_required": True,
            "original_text": baseline,
            "corrected_text": corrected,
            "edits": [
                {
                    **edit.to_dict(),
                    "start": edit.original_start,
                    "end": edit.original_end,
                    "before": edit.original_text,
                    "after": edit.corrected_text,
                }
                for edit in worker_response.edits
            ],
            "edit_ratio": sum(
                max(len(edit.original_text), len(edit.corrected_text))
                for edit in worker_response.edits
            )
            / max(1, len(baseline)),
            "model_id": worker_response.model_id,
            "model_revision": worker_response.model_revision,
            "engine_id": worker_response.engine_id,
            "prompt_version": worker_response.prompt_version,
            "worker_task_id": task_id,
            "execution_owner": "worker",
        }
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_corrector": {
                "execution_owner": "worker",
                "execution_path": "generative_corrector_worker",
                "status": worker_response.status,
                "reason_code": reason_code,
                "model_id": worker_response.model_id,
                "worker_task_id": task_id,
            },
        }
        return VoiceGenerativeCorrectorOutcome(
            result=updated,
            applied=corrected != baseline,
            reason_code=reason_code,
        )

    def _finish_safely(self, task_id: str, *, status: str, reason_code: str) -> None:
        try:
            self._task_tracker.finish(task_id, status=status, reason_code=reason_code)
        except Exception:
            return

    @staticmethod
    def _fallback(
        result: Mapping[str, Any],
        reason_code: str,
        *,
        baseline: str,
        task_id: str | None = None,
    ) -> VoiceGenerativeCorrectorOutcome:
        updated = dict(result)
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_corrector": {
                "execution_owner": "worker",
                "execution_path": "generative_corrector_worker",
                "status": "fallback",
                "reason_code": reason_code,
                **({"worker_task_id": task_id} if task_id else {}),
            },
        }
        if baseline and reason_code != "generative_corrector_disabled":
            updated["original_text"] = baseline
            updated["generative_corrector"] = {
                "schema_version": "ananta.voice-generative-correction.v1",
                "status": "fallback",
                "applied": False,
                "changed": False,
                "review_required": True,
                "reason_code": reason_code,
                "original_text": baseline,
                "corrected_text": baseline,
                "edits": [],
                "execution_owner": "worker",
                **({"worker_task_id": task_id} if task_id else {}),
            }
        return VoiceGenerativeCorrectorOutcome(result=updated, applied=False, reason_code=reason_code)


def _protected_tokens(value: str) -> Counter[str]:
    return Counter(match.group(0) for match in _PROTECTED_TOKEN_RE.finditer(value))


def configured_corrector_models() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in str(settings.voice_generative_corrector_models or "").split(",")
            if _MODEL_ID_RE.fullmatch(item.strip())
        )
    )


def generative_corrector_capabilities(
    worker_port: HttpGenerativeCorrectorWorkerPort | None = None,
) -> list[dict[str, Any]]:
    port = worker_port if worker_port is not None else _configured_worker_port()
    health: Mapping[str, Any] = {}
    if port is not None:
        try:
            health = port.health(timeout_ms=500)
        except Exception:
            health = {}
    ready = health.get("status") == "ready"
    worker_models = {
        str(item)
        for item in health.get("model_ids", [])
        if isinstance(item, str)
    }
    return [
        {
            "id": model_id,
            "role": "generative_corrector",
            "purpose": "transcript_correction",
            "model_type": "causal_lm",
            "local": True,
            "available": bool(ready and model_id in worker_models),
            "status": (
                "ready"
                if ready and model_id in worker_models
                else "model_missing"
                if ready
                else "unavailable"
            ),
            "reason_code": (
                None
                if ready and model_id in worker_models
                else "generative_corrector_model_missing"
                if ready
                else "generative_corrector_worker_unavailable"
            ),
            "capabilities": ["transcript_rewrite", "bounded_edits", "provenance"],
        }
        for model_id in configured_corrector_models()
    ]


def _configured_worker_port() -> VoiceCorrectorWorkerPort | None:
    endpoint = str(settings.voice_generative_corrector_worker_url or "").strip()
    allowed = tuple(
        item.strip()
        for item in str(settings.voice_generative_corrector_worker_allowed_endpoints or "").split(",")
        if item.strip()
    )
    token = str(settings.voice_generative_corrector_worker_token or "").strip()
    origin = str(settings.voice_generative_corrector_hub_origin or "").strip()
    if not endpoint or not allowed or not token or not origin:
        return None
    try:
        return HttpGenerativeCorrectorWorkerPort(
            endpoint=endpoint,
            allowed_endpoints=allowed,
            bearer_token=token,
            hub_origin=origin,
            timeout_ms=max(1, min(int(settings.voice_generative_corrector_timeout_ms), 120_000)),
            max_response_bytes=max(
                1_024,
                min(int(settings.voice_generative_corrector_max_response_bytes), 2 * 1024 * 1024),
            ),
        )
    except (TypeError, ValueError):
        return None


voice_generative_corrector_service = VoiceGenerativeCorrectorService()


def get_voice_generative_corrector_service() -> VoiceGenerativeCorrectorService:
    return voice_generative_corrector_service
