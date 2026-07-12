from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.config import settings
from agent.services.generative_judge_worker_port import HttpGenerativeJudgeWorkerPort
from ananta_contracts.generative_judge_worker import (
    GenerativeJudgeCandidate,
    GenerativeJudgeWorkerPort,
    GenerativeJudgeWorkerRequest,
)


@dataclass(frozen=True)
class VoiceGenerativeJudgeOutcome:
    result: dict[str, Any]
    applied: bool
    reason_code: str


class VoiceGenerativeJudgeTaskTrackerPort(Protocol):
    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
    ) -> str: ...

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class VoiceGenerativeJudgeTaskTracker:
    """Persist only opaque correlation for one Hub-owned judge delegation."""

    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
    ) -> str:
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.voice_task_scope import inherited_voice_task_scope

        correlation = f"{parent_task_id}\0{request_id}\0{content_digest}".encode()
        task_id = f"voice-generative-judge-{hashlib.sha256(correlation).hexdigest()[:32]}"
        inherited_scope = inherited_voice_task_scope(parent_task_id, tenant_id=tenant_id)
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title="Hub-delegated Voice judge worker",
            description="Execute one bounded request in the isolated generative-judge worker.",
            priority="low",
            created_by="hub",
            source="voice_api",
            tags=["voice_transcription", "generative_judge", "worker_delegation"],
            event_type="voice_generative_judge_delegated",
            event_details={"request_id": request_id},
            extra_fields={
                "task_kind": "voice_generative_judge",
                "parent_task_id": parent_task_id,
                "required_capabilities": ["voice_generative_judge_worker"],
                "worker_execution_context": {
                    "voice_generative_judge": {
                        "request_id": request_id,
                        "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
                        **inherited_scope,
                        "content_digest": content_digest,
                        "policy_digest": policy_digest,
                        "persistence_owner": "hub",
                    }
                },
            },
        )
        return task_id

    @staticmethod
    def finish(task_id: str, *, status: str, reason_code: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        succeeded = status == "selected"
        update_local_task_status(
            task_id,
            "completed" if succeeded else "failed",
            status_reason_code=None if succeeded else reason_code,
            status_reason_details={} if succeeded else {"fallback": True},
            verification_status={
                "voice_generative_judge": {
                    "status": "verified" if succeeded else "fallback",
                    "reason_code": reason_code,
                }
            },
            event_type="voice_generative_judge_completed" if succeeded else "voice_generative_judge_failed",
            event_actor="hub",
            event_details={"status": status, "reason_code": reason_code},
        )


class VoiceGenerativeJudgeService:
    """Hub-owned delegation boundary with exact candidate-only fallback."""

    def __init__(
        self,
        worker_port: GenerativeJudgeWorkerPort | None = None,
        task_tracker: VoiceGenerativeJudgeTaskTrackerPort | None = None,
    ) -> None:
        self._worker_port = worker_port
        self._task_tracker = (
            task_tracker if task_tracker is not None else VoiceGenerativeJudgeTaskTracker()
        )

    def apply(
        self,
        result: Mapping[str, Any],
        *,
        effective_configuration: Mapping[str, Any],
        tenant_id: str | None = None,
        parent_task_id: str | None = None,
        request_id: str | None = None,
        deadline_epoch_ms: int | None = None,
    ) -> VoiceGenerativeJudgeOutcome:
        baseline = str(result.get("text") or "")
        feature_flags = effective_configuration.get("feature_flags")
        enabled = (
            effective_configuration.get("correction_policy") == "generative_local"
            and isinstance(feature_flags, Mapping)
            and feature_flags.get("generative_judge") is True
        )
        if not enabled or not baseline:
            return self._fallback(result, "generative_judge_disabled")

        candidates = _candidate_choices(result, baseline=baseline)
        if not candidates:
            return self._fallback(result, "generative_judge_no_candidates")

        worker_port = self._worker_port if self._worker_port is not None else _configured_worker_port()
        if worker_port is None:
            return self._fallback(result, "generative_judge_unavailable")
        if not tenant_id or not parent_task_id or not request_id:
            return self._fallback(result, "generative_judge_correlation_missing")

        try:
            candidate_payload = json.dumps(
                [candidate.to_dict() for candidate in candidates],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            policy_payload = json.dumps(
                dict(effective_configuration),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError):
            return self._fallback(result, "generative_judge_failed")
        timeout_ms = max(1, min(int(settings.voice_generative_judge_timeout_ms), 60_000))
        now_ms = time.time_ns() // 1_000_000
        local_deadline_ms = now_ms + timeout_ms
        if deadline_epoch_ms is not None:
            if isinstance(deadline_epoch_ms, bool) or not isinstance(deadline_epoch_ms, int):
                return self._fallback(result, "generative_judge_deadline_expired")
            local_deadline_ms = min(local_deadline_ms, deadline_epoch_ms)
        if local_deadline_ms <= now_ms:
            return self._fallback(result, "generative_judge_deadline_expired")
        task_id: str | None = None
        try:
            task_id = self._task_tracker.start(
                tenant_id=tenant_id,
                parent_task_id=parent_task_id,
                request_id=request_id,
                content_digest=hashlib.sha256(candidate_payload).hexdigest(),
                policy_digest=hashlib.sha256(policy_payload).hexdigest(),
            )
            worker_request = GenerativeJudgeWorkerRequest(
                request_id=request_id,
                task_id=task_id,
                region_id="full-transcript",
                candidates=candidates,
                baseline_choice_id=candidates[0].choice_id,
                deadline_epoch_ms=local_deadline_ms,
            )
            worker_response = worker_port.execute(worker_request)
            worker_response.validate_for(worker_request)
        except Exception:
            if task_id is not None:
                self._finish_safely(task_id, status="failed", reason_code="generative_judge_failed")
            return self._fallback(result, "generative_judge_failed", task_id=task_id)
        reason_code = worker_response.reason_code or "generative_judge_selected"
        try:
            self._task_tracker.finish(task_id, status=worker_response.status, reason_code=reason_code)
        except Exception:
            return self._fallback(result, "generative_judge_tracking_failed", task_id=task_id)
        selected = {
            candidate.choice_id: candidate.text for candidate in candidates
        }.get(worker_response.choice_id or "")
        if worker_response.status != "selected" or selected is None:
            return self._fallback(result, reason_code, task_id=task_id)
        updated = dict(result)
        updated["text"] = selected
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_judge": {
                "execution_owner": "worker",
                "execution_path": "generative_judge_worker",
                "restricted_inference_result": False,
                "status": worker_response.status,
                "reason_code": worker_response.reason_code,
                "candidate_count": len(candidates),
                "selected_choice_id": worker_response.choice_id,
                "worker_task_id": task_id,
            },
        }
        return VoiceGenerativeJudgeOutcome(
            result=updated,
            applied=selected != baseline,
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
        task_id: str | None = None,
    ) -> VoiceGenerativeJudgeOutcome:
        updated = dict(result)
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_judge": {
                "execution_owner": "worker",
                "execution_path": "generative_judge_worker",
                "restricted_inference_result": False,
                "status": "fallback",
                "reason_code": reason_code,
                **({"worker_task_id": task_id} if task_id else {}),
            },
        }
        return VoiceGenerativeJudgeOutcome(result=updated, applied=False, reason_code=reason_code)


def _candidate_choices(
    result: Mapping[str, Any],
    *,
    baseline: str,
) -> tuple[GenerativeJudgeCandidate, ...]:
    if not baseline or len(baseline) > 8_000 or "\x00" in baseline:
        return ()
    values = [baseline]
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or candidate.get("status", "succeeded") != "succeeded":
                continue
            text = str(candidate.get("text") or "")
            if text.strip() and len(text) <= 8_000 and "\x00" not in text and text not in values:
                values.append(text)
            if len(values) >= 64:
                break
    return tuple(
        GenerativeJudgeCandidate(choice_id=f"candidate-{index:03d}", text=text)
        for index, text in enumerate(values)
    )


def _configured_worker_port() -> GenerativeJudgeWorkerPort | None:
    endpoint = str(settings.voice_generative_judge_worker_url or "").strip()
    allowed = tuple(
        item.strip()
        for item in str(settings.voice_generative_judge_worker_allowed_endpoints or "").split(",")
        if item.strip()
    )
    token = str(settings.voice_generative_judge_worker_token or "").strip()
    origin = str(settings.voice_generative_judge_hub_origin or "").strip()
    if not endpoint or not allowed or not token or not origin:
        return None
    try:
        return HttpGenerativeJudgeWorkerPort(
            endpoint=endpoint,
            allowed_endpoints=allowed,
            bearer_token=token,
            hub_origin=origin,
            timeout_ms=max(1, min(int(settings.voice_generative_judge_timeout_ms), 60_000)),
            max_response_bytes=max(
                1024,
                min(int(settings.voice_generative_judge_max_response_bytes), 1024 * 1024),
            ),
        )
    except (TypeError, ValueError):
        return None


voice_generative_judge_service = VoiceGenerativeJudgeService()


def get_voice_generative_judge_service() -> VoiceGenerativeJudgeService:
    return voice_generative_judge_service
