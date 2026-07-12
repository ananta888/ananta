from __future__ import annotations

import hashlib
import json
import time
import uuid

from agent.services.voice_generative_judge_service import VoiceGenerativeJudgeService
from ananta_contracts.generative_judge_worker import (
    GenerativeJudgeWorkerRequest,
    GenerativeJudgeWorkerResponse,
)


class _Port:
    def __init__(self, choice_id: str | Exception = "candidate-001") -> None:
        self.choice_id = choice_id
        self.requests: list[GenerativeJudgeWorkerRequest] = []

    def execute(self, request: GenerativeJudgeWorkerRequest) -> GenerativeJudgeWorkerResponse:
        self.requests.append(request)
        if isinstance(self.choice_id, Exception):
            raise self.choice_id
        return GenerativeJudgeWorkerResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            status="selected",
            choice_id=self.choice_id,
            reason_code=None,
            engine_id="fixture-engine",
        )


class _Tracker:
    def __init__(self) -> None:
        self.started = 0
        self.finished: list[tuple[str, str, str]] = []

    def start(self, **_kwargs) -> str:
        self.started += 1
        return "voice-generative-judge-child"

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        self.finished.append((task_id, status, reason_code))


class _FailingTracker(_Tracker):
    def __init__(self, *, fail_start: bool = False, fail_finish: bool = False) -> None:
        super().__init__()
        self.fail_start = fail_start
        self.fail_finish = fail_finish

    def start(self, **kwargs) -> str:
        if self.fail_start:
            raise RuntimeError("tracking unavailable")
        return super().start(**kwargs)

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        if self.fail_finish:
            raise RuntimeError("tracking unavailable")
        super().finish(task_id, status=status, reason_code=reason_code)


def _result() -> dict:
    return {
        "text": "baseline",
        "candidates": [
            {"candidate_id": "base", "status": "succeeded", "text": "baseline"},
            {"candidate_id": "alt", "status": "succeeded", "text": "candidate"},
        ],
        "decision_trace": {"fusion": "consensus"},
    }


def _configuration() -> dict:
    return {
        "correction_policy": "generative_local",
        "feature_flags": {"generative_judge": True},
    }


def _apply(service: VoiceGenerativeJudgeService, result: dict | None = None):
    return service.apply(
        result or _result(),
        effective_configuration=_configuration(),
        tenant_id="tenant-a",
        parent_task_id="voice-parent-task",
        request_id="voice-request-1",
    )


def test_hub_delegates_to_worker_and_selects_only_a_known_candidate() -> None:
    port = _Port()
    tracker = _Tracker()

    outcome = _apply(VoiceGenerativeJudgeService(worker_port=port, task_tracker=tracker))

    assert outcome.applied is True
    assert outcome.result["text"] == "candidate"
    trace = outcome.result["decision_trace"]["generative_judge"]
    assert trace["execution_owner"] == "worker"
    assert trace["execution_path"] == "generative_judge_worker"
    assert trace["selected_choice_id"] == "candidate-001"
    assert port.requests[0].task_id == "voice-generative-judge-child"
    assert tracker.finished == [
        ("voice-generative-judge-child", "selected", "generative_judge_selected")
    ]


def test_unprovenanced_selection_and_timeout_preserve_exact_baseline() -> None:
    for selection in ("candidate-999", TimeoutError("timeout")):
        tracker = _Tracker()
        outcome = _apply(
            VoiceGenerativeJudgeService(worker_port=_Port(selection), task_tracker=tracker)
        )
        assert outcome.applied is False
        assert outcome.result["text"] == "baseline"
        assert outcome.reason_code == "generative_judge_failed"
        assert tracker.finished[-1][1] == "failed"


def test_disabled_policy_never_calls_worker_or_task_tracker() -> None:
    port = _Port()
    tracker = _Tracker()
    configuration = _configuration()
    configuration["feature_flags"]["generative_judge"] = False

    outcome = VoiceGenerativeJudgeService(worker_port=port, task_tracker=tracker).apply(
        _result(),
        effective_configuration=configuration,
    )

    assert outcome.result["text"] == "baseline"
    assert outcome.reason_code == "generative_judge_disabled"
    assert outcome.result["decision_trace"]["generative_judge"]["execution_owner"] == "worker"
    assert port.requests == []
    assert tracker.started == 0


def test_enabled_worker_call_requires_hub_task_correlation() -> None:
    port = _Port()
    outcome = VoiceGenerativeJudgeService(worker_port=port, task_tracker=_Tracker()).apply(
        _result(),
        effective_configuration=_configuration(),
    )

    assert outcome.reason_code == "generative_judge_correlation_missing"
    assert port.requests == []


def test_oversized_or_nul_baseline_and_candidates_never_break_main_voice_result() -> None:
    for baseline in ("x" * 8_001, "baseline\x00invalid"):
        result = _result()
        result["text"] = baseline
        result["candidates"].append(
            {"candidate_id": "invalid", "status": "succeeded", "text": "candidate\x00invalid"}
        )
        port = _Port()

        outcome = _apply(
            VoiceGenerativeJudgeService(worker_port=port, task_tracker=_Tracker()),
            result,
        )

        assert outcome.result["text"] == baseline
        assert outcome.reason_code == "generative_judge_no_candidates"
        assert port.requests == []


def test_candidate_selection_preserves_candidate_bytes_including_whitespace() -> None:
    result = _result()
    result["candidates"][1]["text"] = "  candidate with spacing  "

    outcome = _apply(
        VoiceGenerativeJudgeService(worker_port=_Port(), task_tracker=_Tracker()),
        result,
    )

    assert outcome.result["text"] == "  candidate with spacing  "


def test_task_tracking_failure_never_breaks_or_mutates_consensus_baseline() -> None:
    for tracker in (_FailingTracker(fail_start=True), _FailingTracker(fail_finish=True)):
        outcome = _apply(VoiceGenerativeJudgeService(worker_port=_Port(), task_tracker=tracker))

        assert outcome.applied is False
        assert outcome.result["text"] == "baseline"
        assert outcome.reason_code in {
            "generative_judge_failed",
            "generative_judge_tracking_failed",
        }


def test_parent_voice_deadline_narrows_worker_budget_and_expired_budget_never_dispatches() -> None:
    port = _Port()
    tracker = _Tracker()
    parent_deadline = time.time_ns() // 1_000_000 + 250
    service = VoiceGenerativeJudgeService(worker_port=port, task_tracker=tracker)

    selected = service.apply(
        _result(),
        effective_configuration=_configuration(),
        tenant_id="tenant-a",
        parent_task_id="voice-parent-task",
        request_id="voice-request-deadline",
        deadline_epoch_ms=parent_deadline,
    )
    expired_port = _Port()
    expired_tracker = _Tracker()
    expired = VoiceGenerativeJudgeService(
        worker_port=expired_port,
        task_tracker=expired_tracker,
    ).apply(
        _result(),
        effective_configuration=_configuration(),
        tenant_id="tenant-a",
        parent_task_id="voice-parent-task",
        request_id="voice-request-expired",
        deadline_epoch_ms=1,
    )

    assert selected.applied is True
    assert port.requests[0].deadline_epoch_ms == parent_deadline
    assert expired.result["text"] == "baseline"
    assert expired.reason_code == "generative_judge_deadline_expired"
    assert expired_port.requests == []
    assert expired_tracker.started == 0


def test_hub_task_tracks_judge_without_persisting_candidate_text(app) -> None:
    from agent.repository import task_repo
    from agent.services.task_queue_service import get_task_queue_service

    secret = "PRIVATE-SPOKEN-CANDIDATE"
    result = _result()
    result["candidates"][1]["text"] = secret
    request_id = f"voice-judge-request-{uuid.uuid4().hex}"
    parent_task_id = f"voice-parent-{uuid.uuid4().hex}"
    owner_hash = hashlib.sha256(b"owner-subject").hexdigest()

    with app.app_context():
        get_task_queue_service().ingest_task(
            task_id=parent_task_id,
            status="in_progress",
            title="Voice parent",
            description="Parent voice task",
            created_by="hub",
            source="voice_api",
            extra_fields={
                "task_kind": "voice_transcription",
                "worker_execution_context": {
                    "voice_transcription": {
                        "tenant_scope_hash": hashlib.sha256(b"private-tenant").hexdigest(),
                        "owner_subject_hash": owner_hash,
                        "profile_id": "profile-private",
                    }
                },
            },
        )
        outcome = VoiceGenerativeJudgeService(worker_port=_Port()).apply(
            result,
            effective_configuration=_configuration(),
            tenant_id="private-tenant",
            parent_task_id=parent_task_id,
            request_id=request_id,
        )
        tracked = next(
            task
            for task in task_repo.get_all()
            if task.task_kind == "voice_generative_judge" and task.parent_task_id == parent_task_id
        )

    assert outcome.result["text"] == secret
    assert tracked.status == "completed"
    context = tracked.worker_execution_context["voice_generative_judge"]
    assert context["owner_subject_hash"] == owner_hash
    assert context["profile_id"] == "profile-private"
    serialized = json.dumps(tracked.model_dump(), default=str)
    assert secret not in serialized
    assert "private-tenant" not in serialized
