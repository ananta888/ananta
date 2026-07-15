from __future__ import annotations

import pytest

from agent.config import settings
from agent.services.voice_generative_corrector_service import (
    VoiceGenerativeCorrectorService,
    generative_corrector_capabilities,
    generative_corrector_default_capability,
    generative_corrector_provider_capabilities,
    resolve_auto_corrector_configuration,
    resolve_inherited_corrector_configuration,
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
        "generative_corrector_provider": "embedded",
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
    assert correction["provider_id"] == "embedded"
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


def test_hub_qualifies_an_allowlisted_external_provider_model_before_dispatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "voice_generative_corrector_providers",
        "embedded,ollama,lmstudio",
    )
    port = _Port()
    tracker = _Tracker()
    configuration = _configuration()
    configuration["generative_corrector_provider"] = "ollama"
    configuration["generative_corrector_model"] = "qwen2.5:7b"

    outcome = VoiceGenerativeCorrectorService(worker_port=port, task_tracker=tracker).apply(
        {"text": "hallo welt"},
        effective_configuration=configuration,
        tenant_id="tenant-a",
        parent_task_id="voice-parent-task",
        request_id="voice-request-1",
        language="de",
    )

    assert outcome.applied is True
    assert port.requests[0].model_id == "ollama:qwen2.5:7b"
    assert tracker.started[0]["model_id"] == "ollama:qwen2.5:7b"
    correction = outcome.result["generative_corrector"]
    assert correction["provider_id"] == "ollama"
    assert correction["model_id"] == "ollama:qwen2.5:7b"


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


def test_capabilities_project_discovered_provider_models_and_manual_support(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "voice_generative_corrector_providers",
        "embedded,ollama,lmstudio",
    )

    class _HealthPort:
        def health(self, *, timeout_ms: int):
            assert timeout_ms == 500
            return {
                "status": "ready",
                "model_ids": [
                    "gemma-2b-it",
                    "ollama:qwen2.5:7b",
                    "lmstudio:org/model",
                ],
                "provider_ids": ["ollama", "lmstudio"],
                "ready_provider_ids": ["ollama", "lmstudio"],
            }

    port = _HealthPort()
    models = generative_corrector_capabilities(port)  # type: ignore[arg-type]
    providers = generative_corrector_provider_capabilities(  # type: ignore[arg-type]
        models,
        port,
    )

    by_key = {(item["provider"], item["id"]): item for item in models}
    assert by_key[("ollama", "qwen2.5:7b")]["available"] is True
    assert by_key[("ollama", "qwen2.5:7b")]["worker_model_id"] == "ollama:qwen2.5:7b"
    assert by_key[("lmstudio", "org/model")]["available"] is True
    by_provider = {item["id"]: item for item in providers}
    assert by_provider["ollama"]["available"] is True
    assert by_provider["ollama"]["supports_manual_model"] is True
    assert by_provider["lmstudio"]["supports_manual_model"] is True
    assert by_provider["embedded"]["supports_manual_model"] is False


def test_offline_provider_is_not_available_but_manual_model_stays_configurable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "voice_generative_corrector_providers",
        "embedded,ollama,lmstudio",
    )
    health = {
        # The hybrid worker itself remains ready through its embedded model.
        "status": "ready",
        "model_ids": ["gemma-2b-it", "lmstudio:stale/model"],
        "provider_ids": ["ollama", "lmstudio"],
        "ready_provider_ids": ["ollama"],
    }

    models = generative_corrector_capabilities(worker_health=health)
    providers = generative_corrector_provider_capabilities(
        models,
        worker_health=health,
    )

    by_key = {(item["provider"], item["id"]): item for item in models}
    assert by_key[("lmstudio", "stale/model")]["available"] is False
    assert by_key[("lmstudio", "stale/model")]["reason_code"] == ("generative_corrector_provider_unavailable")
    by_provider = {item["id"]: item for item in providers}
    assert by_provider["lmstudio"] == {
        "id": "lmstudio",
        "display_name": "LM Studio",
        "available": False,
        "supports_manual_model": True,
        "reason_code": "generative_corrector_provider_unavailable",
    }
    assert by_provider["ollama"]["available"] is True
    assert by_provider["ollama"]["supports_manual_model"] is True


def test_inherit_resolves_the_general_provider_and_model_without_persisting_an_endpoint() -> None:
    resolved = resolve_inherited_corrector_configuration(
        {
            "correction_policy": "generative_rewrite",
            "generative_corrector_provider": "inherit",
            "generative_corrector_model": "",
        },
        {"llm_config": {"provider": "ollama", "model": "qwen2.5:7b"}},
    )

    assert resolved["generative_corrector_provider"] == "ollama"
    assert resolved["generative_corrector_model"] == "qwen2.5:7b"
    assert resolved["generative_corrector_inherited"] is True
    assert resolved["generative_corrector_inherited_source"] == "agent_config.llm_config"
    assert not any("url" in key or "token" in key or "key" in key for key in resolved)

    inherited_default = generative_corrector_default_capability(
        {"llm_config": {"provider": "ollama", "model": "qwen2.5:7b"}},
        correction_models=[],
        correction_providers=[
            {
                "id": "ollama",
                "available": True,
                "supports_manual_model": True,
            }
        ],
    )
    assert inherited_default == {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "source": "agent_config.llm_config",
        "available": True,
    }


def test_inherited_manual_model_is_unavailable_when_qualification_exceeds_contract_limit() -> None:
    # The raw ID still fits the public field, but the worker receives the
    # provider-qualified ID ("lmstudio:<id>"), whose total limit is 192.
    raw_model_id = "m" * 184

    inherited_default = generative_corrector_default_capability(
        {"llm_config": {"provider": "lmstudio", "model": raw_model_id}},
        correction_models=[],
        correction_providers=[
            {
                "id": "lmstudio",
                "available": True,
                "supports_manual_model": True,
            }
        ],
    )

    assert inherited_default == {
        "provider": "lmstudio",
        "model": raw_model_id,
        "source": "agent_config.llm_config",
        "available": False,
    }


@pytest.mark.parametrize(
    ("provider_id", "first_model", "second_model"),
    [
        ("lmstudio", "zeta/model", "alpha/model"),
        ("ollama", "zeta:7b", "alpha:3b"),
    ],
)
def test_auto_general_default_selects_the_first_available_provider_model_for_execution(
    provider_id: str,
    first_model: str,
    second_model: str,
) -> None:
    agent_configuration = {
        "llm_config": {"provider": provider_id, "model": "auto"},
    }
    correction_models = [
        {
            "provider": provider_id,
            "id": "unavailable/model",
            "available": False,
        },
        {
            "provider": "embedded",
            "id": "gemma-2b-it",
            "available": True,
        },
        {
            "provider": provider_id,
            "id": first_model,
            "available": True,
        },
        {
            "provider": provider_id,
            "id": second_model,
            "available": True,
        },
    ]
    inherited = resolve_inherited_corrector_configuration(
        {
            "correction_policy": "generative_rewrite",
            "generative_corrector_provider": "inherit",
            "generative_corrector_model": "",
        },
        agent_configuration,
    )

    execution_snapshot = resolve_auto_corrector_configuration(inherited, correction_models)

    assert execution_snapshot["generative_corrector_provider"] == provider_id
    assert execution_snapshot["generative_corrector_model"] == first_model
    assert execution_snapshot["generative_corrector_requested_model"] == "auto"
    assert execution_snapshot["generative_corrector_auto_resolved"] is True
    assert execution_snapshot["generative_corrector_inherited_source"] == "agent_config.llm_config"

    projected_default = generative_corrector_default_capability(
        agent_configuration,
        correction_models=correction_models,
        correction_providers=[
            {
                "id": provider_id,
                "available": True,
                "supports_manual_model": True,
            }
        ],
    )
    assert projected_default == {
        "provider": provider_id,
        "model": first_model,
        "source": "agent_config.llm_config",
        "available": True,
        "configured_model": "auto",
    }


@pytest.mark.parametrize("provider_id", ["lmstudio", "ollama"])
def test_auto_general_default_stays_unresolved_and_unavailable_without_a_matching_catalog(
    provider_id: str,
) -> None:
    agent_configuration = {
        "llm_config": {"provider": provider_id, "model": "auto"},
    }
    correction_models = [
        {
            "provider": provider_id,
            "id": "unavailable/model",
            "available": False,
        },
        {
            "provider": "embedded",
            "id": "gemma-2b-it",
            "available": True,
        },
    ]
    inherited = resolve_inherited_corrector_configuration(
        {
            "generative_corrector_provider": "inherit",
            "generative_corrector_model": "",
        },
        agent_configuration,
    )

    execution_snapshot = resolve_auto_corrector_configuration(inherited, correction_models)

    assert execution_snapshot["generative_corrector_provider"] == provider_id
    assert execution_snapshot["generative_corrector_model"] == "auto"
    assert "generative_corrector_requested_model" not in execution_snapshot
    assert "generative_corrector_auto_resolved" not in execution_snapshot

    projected_default = generative_corrector_default_capability(
        agent_configuration,
        correction_models=correction_models,
        correction_providers=[
            {
                "id": provider_id,
                "available": True,
                "supports_manual_model": True,
            }
        ],
    )
    assert projected_default == {
        "provider": provider_id,
        "model": "auto",
        "source": "agent_config.llm_config",
        "available": False,
    }
