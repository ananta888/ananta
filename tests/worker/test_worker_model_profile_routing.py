from agent.routes.config.shared import normalize_opencode_runtime_config
from agent.services.model_profile_loader import ModelProfile
from agent.services.routing_decision_service import get_routing_decision_service


def test_opencode_runtime_accepts_target_profile_and_model():
    cfg = normalize_opencode_runtime_config(
        {
            "tool_mode": "readonly",
            "execution_mode": "interactive_terminal",
            "target_profile": "local-planner",
            "target_provider": "ollama",
            "target_model": "qwen2.5-coder:7b",
        }
    )

    assert cfg["target_profile"] == "local-planner"
    assert cfg["target_provider"] == "ollama"
    assert cfg["target_model"] == "qwen2.5-coder:7b"


def test_worker_runtime_targets_can_reference_model_profile():
    service = get_routing_decision_service()
    profile = ModelProfile(
        profile_id="local-coder",
        provider_id="ollama",
        model="qwen2.5-coder:7b",
        model_role="coder",
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
    )
    worker = {
        "name": "opencode-local",
        "capabilities": ["coder", "coding"],
        "runtime_targets": [{"runtime_kind": "opencode", "target_profile": "local-coder"}],
    }

    decision = service.worker_model_profile_decision(worker=worker, profile=profile)

    assert decision["allowed"] is True
    assert decision["target_match"] == "target_profile"


def test_remote_worker_is_blocked_for_secret_context():
    service = get_routing_decision_service()
    profile = ModelProfile(
        profile_id="cloud-reviewer",
        provider_id="openrouter",
        model="anthropic/claude",
        model_role="reviewer",
        local=False,
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )
    worker = {
        "name": "hermes-remote",
        "capabilities": ["reviewer"],
        "runtime_targets": [{"runtime_kind": "remote_hub", "target_profile": "cloud-reviewer"}],
    }

    decision = service.worker_model_profile_decision(
        worker=worker,
        profile=profile,
        context_contains_secret=True,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "secret_context_not_allowed_for_remote_or_secret_blocking_profile"
