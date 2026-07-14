from __future__ import annotations

from agent.services.voice_generative_corrector_service import (
    VoiceGenerativeCorrectorService,
    generative_corrector_capabilities,
)
from ananta_contracts.voice_corrector_worker import (
    VoiceCorrectorWorkerRequest,
    VoiceCorrectorWorkerResponse,
    build_edits,
)


class _Port:
    def __init__(self, corrected_text: str = "Hallo Welt.") -> None:
        self.corrected_text = corrected_text
        self.requests: list[VoiceCorrectorWorkerRequest] = []

    def execute(self, request: VoiceCorrectorWorkerRequest) -> VoiceCorrectorWorkerResponse:
        self.requests.append(request)
        edits = build_edits(request.original_text, self.corrected_text)
        return VoiceCorrectorWorkerResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            status="unchanged" if request.original_text == self.corrected_text else "corrected",
            original_text=request.original_text,
            corrected_text=self.corrected_text,
            edits=edits,
            reason_code=None,
            model_id=request.model_id,
            model_revision="sha256-fixture",
            engine_id="fixture-engine",
            prompt_version="prompt-v1",
        )


class _Tracker:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.finished: list[tuple[str, str, str]] = []

    def start(self, **kwargs) -> str:
        self.started.append(kwargs)
        return "voice-generative-corrector-child"

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        self.finished.append((task_id, status, reason_code))


def _configuration() -> dict:
    return {
        "correction_policy": "generative_rewrite",
        "generative_corrector_model": "gemma-2b-it",
        "generative_corrector_max_edit_ratio": 0.35,
        "feature_flags": {"generative_corrector": True},
    }


def _apply(service: VoiceGenerativeCorrectorService, *, text: str = "hallo welt"):
    return service.apply(
        {"text": text, "decision_trace": {"asr": "vosk"}},
        effective_configuration=_configuration(),
        tenant_id="tenant-a",
        parent_task_id="voice-parent-task",
        request_id="voice-request-1",
        language="de",
    )


def test_hub_delegates_rewrite_and_preserves_original_with_provenance() -> None:
    port = _Port()
    tracker = _Tracker()

    outcome = _apply(VoiceGenerativeCorrectorService(worker_port=port, task_tracker=tracker))

    assert outcome.applied is True
    assert outcome.result["original_text"] == "hallo welt"
    assert outcome.result["text"] == "Hallo Welt."
    correction = outcome.result["generative_corrector"]
    assert correction["model_id"] == "gemma-2b-it"
    assert correction["model_revision"] == "sha256-fixture"
    assert correction["changed"] is True
    assert correction["review_required"] is True
    assert correction["edits"][0]["before"]
    assert port.requests[0].language == "de"
    assert tracker.finished == [
        (
            "voice-generative-corrector-child",
            "corrected",
            "generative_corrector_corrected",
        )
    ]


def test_disabled_or_unallowlisted_policy_never_dispatches() -> None:
    port = _Port()
    tracker = _Tracker()
    disabled = _configuration()
    disabled["feature_flags"]["generative_corrector"] = False
    service = VoiceGenerativeCorrectorService(worker_port=port, task_tracker=tracker)

    disabled_outcome = service.apply(
        {"text": "baseline"},
        effective_configuration=disabled,
    )
    unknown = _configuration()
    unknown["generative_corrector_model"] = "unknown-model"
    unknown_outcome = service.apply(
        {"text": "baseline"},
        effective_configuration=unknown,
    )

    assert disabled_outcome.result["text"] == "baseline"
    assert unknown_outcome.result["text"] == "baseline"
    assert unknown_outcome.reason_code == "generative_corrector_model_not_allowlisted"
    assert port.requests == []
    assert tracker.started == []


def test_worker_failure_or_protected_token_change_falls_back_to_exact_original() -> None:
    tracker = _Tracker()
    changed_number = VoiceGenerativeCorrectorService(
        worker_port=_Port("Version 43 ist fertig."),
        task_tracker=tracker,
    )

    outcome = _apply(changed_number, text="Version 42 ist fertig")

    assert outcome.applied is False
    assert outcome.result["text"] == "Version 42 ist fertig"
    assert outcome.result["original_text"] == "Version 42 ist fertig"
    assert outcome.result["generative_corrector"]["changed"] is False
    assert tracker.finished == [
        (
            "voice-generative-corrector-child",
            "failed",
            "generative_corrector_protected_token_changed",
        )
    ]


def test_capabilities_require_verified_worker_readiness_and_catalog_membership() -> None:
    class _HealthPort:
        def health(self, *, timeout_ms: int):
            assert timeout_ms == 500
            return {
                "status": "ready",
                "model_ids": ["gemma-2b-it"],
            }

    capabilities = generative_corrector_capabilities(_HealthPort())  # type: ignore[arg-type]

    by_id = {item["id"]: item for item in capabilities}
    assert by_id["gemma-2b-it"]["available"] is True
    assert by_id["phi-3-mini-instruct"]["available"] is False
    assert by_id["phi-3-mini-instruct"]["reason_code"] == "generative_corrector_model_missing"

    class _UnavailableHealthPort:
        def health(self, *, timeout_ms: int):
            raise TimeoutError

    unavailable = generative_corrector_capabilities(  # type: ignore[arg-type]
        _UnavailableHealthPort()
    )
    assert all(item["available"] is False for item in unavailable)
