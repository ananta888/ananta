from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.services.model_profile_loader import ALLOWED_MODEL_ROLES
from agent.services.model_routing_contract import (
    ModelRoutingConfig,
    ModelRoutingContractError,
    build_model_routing_context,
    extract_model_routing_from_task,
    has_model_routing_declaration,
    model_routing_policy_failure_metadata,
)
from agent.visual_process.models import ModelRoutingConfig as VisualModelRoutingConfig


def test_visual_process_reexports_central_model_routing_contract() -> None:
    assert VisualModelRoutingConfig is ModelRoutingConfig
    assert "reasoning" in ALLOWED_MODEL_ROLES


def test_model_routing_serialization_is_an_explicit_allowlist() -> None:
    routing = ModelRoutingConfig.from_mapping(
        {
            "model_role": "reasoning",
            "preferred_profile_id": "local-gemma",
            "allow_cloud": False,
            "unreviewed_provider_url": "https://example.invalid",
        }
    )

    assert routing.as_metadata()["model_role"] == "reasoning"
    assert routing.as_metadata()["preferred_profile_id"] == "local-gemma"
    assert "unreviewed_provider_url" not in routing.as_metadata()


@pytest.mark.parametrize(
    "raw",
    [
        {"context_recovery_strategies": ["stop", "compact_context"]},
        {"context_recovery_strategies": ["compact_context", "compact_context"]},
        {
            "context_recovery_strategies": [
                "propose_task_plan",
                "segment_planning",
                "require_approval",
            ],
        },
        {
            "context_recovery_strategies": [
                "segment_planning",
                "stop",
            ],
        },
        {
            "context_recovery_strategies": ["propose_task_plan"],
            "require_approval_for_generated_plan": False,
        },
        {"max_estimated_cost": -0.01},
        {"max_estimated_cost": "0.01"},
        {"allow_cloud": "false"},
    ],
)
def test_model_routing_rejects_unsafe_or_ambiguous_values(raw: dict) -> None:
    with pytest.raises(ValidationError):
        ModelRoutingConfig.from_mapping(raw)


def test_task_extraction_prefers_fenced_native_node_and_drops_unknown_fields() -> None:
    task = {
        "model_routing": {"preferred_profile_id": "outer-untrusted"},
        "worker_execution_context": {
            "schema": "ananta.native_graph_worker_context.v1",
            "native_node_command": {
                "node": {
                    "metadata": {
                        "model_routing": {
                            "model_role": "reasoning",
                            "preferred_profile_id": "local-gemma",
                            "fallback_group_id": "phi-to-gemma",
                            "unexpected_runtime_field": "discard-me",
                        }
                    }
                }
            },
        },
    }

    routing = extract_model_routing_from_task(task)

    assert routing is not None
    assert routing.preferred_profile_id == "local-gemma"
    assert "unexpected_runtime_field" not in routing.as_metadata()


def test_task_extraction_supports_object_and_workflow_step_shapes() -> None:
    task = SimpleNamespace(
        task_kind="planning",
        worker_execution_context={
            "workflow_step": {
                "metadata": {
                    "model_routing": {
                        "preferred_profile_id": "local-phi",
                        "requires_json": True,
                    }
                }
            }
        },
    )

    routing = extract_model_routing_from_task(task)

    assert routing is not None
    assert routing.preferred_profile_id == "local-phi"


def test_task_extraction_supports_object_native_command_shape() -> None:
    command = SimpleNamespace(
        node=SimpleNamespace(
            metadata={
                "model_routing": {
                    "preferred_profile_id": "local-phi",
                    "allow_cloud": False,
                }
            }
        )
    )
    task = SimpleNamespace(
        worker_execution_context={"native_node_command": command}
    )

    routing = extract_model_routing_from_task(task)

    assert routing is not None
    assert routing.preferred_profile_id == "local-phi"
    assert routing.allow_cloud is False


def test_invalid_high_precedence_routing_does_not_fall_through() -> None:
    task = {
        "model_routing": {"preferred_profile_id": "outer"},
        "worker_execution_context": {
            "native_node_command": {
                "node": {"metadata": {"model_routing": "not-an-object"}}
            }
        },
    }

    assert has_model_routing_declaration(task) is True
    assert extract_model_routing_from_task(task) is None
    with pytest.raises(ModelRoutingContractError) as exc_info:
        build_model_routing_context(task)
    metadata = model_routing_policy_failure_metadata(exc_info.value)
    assert metadata["fallback_decisions"][0]["trigger"] == "policy_blocked"
    assert metadata["fallback_decisions"][0]["terminal"] is True


def test_routing_context_builder_uses_typed_task_routing() -> None:
    task = {
        "task_kind": "planning",
        "team_id": "team-a",
        "model_routing": {
            "model_role": "reasoning",
            "preferred_profile_id": "local-gemma",
            "fallback_group_id": "phi-to-gemma",
            "required_capabilities": ["supports_json"],
            "allow_cloud": False,
            "max_estimated_cost": 0.0,
        },
    }

    context = build_model_routing_context(task, context_text="bounded context")

    assert context is not None
    assert context.model_role == "reasoning"
    assert context.request_profile_id == "local-gemma"
    assert context.fallback_group_id == "phi-to-gemma"
    assert context.requires_json is True
    assert context.allow_cloud is False
    assert context.task_kind == "planning"
    assert context.team_id == "team-a"


def test_routing_context_builder_returns_none_without_declaration() -> None:
    assert build_model_routing_context({"task_kind": "coding"}) is None
