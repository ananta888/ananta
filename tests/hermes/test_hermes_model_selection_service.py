from __future__ import annotations

import json
from pathlib import Path

from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_model_selection import HermesModelSelectionService, HermesRoutingContext


def _cfg(**overrides: object) -> HermesAdapterConfig:
    payload = {
        "enabled": True,
        "feature_flag_enabled": True,
        "base_url": "http://localhost:1234",
        "default_model": "z-ai/glm-4.5-air:free",
        "task_kind_models": {
            "plan_only": "z-ai/glm-4.5-air:free",
            "summarize": "z-ai/glm-4.5-air:free",
            "review": "qwen/qwen3-coder:free",
            "patch_propose": "qwen/qwen3-coder:free",
        },
        "fallback_free_models": {
            "plan_only": ["z-ai/glm-4.5-air:free"],
            "review": ["qwen/qwen3-coder:free"],
            "default": ["moonshotai/kimi-k2:free"],
        },
        "model_selection_policy": {
            "require_free_model_suffix": True,
            "allow_fallback_on_unavailable": True,
        },
    }
    payload.update(overrides)
    return HermesAdapterConfig(**payload)


def test_select_prefers_task_specific_model() -> None:
    service = HermesModelSelectionService()
    result = service.select_model(config=_cfg(), context=HermesRoutingContext(task_kind="review"))
    assert result.selected_model == "qwen/qwen3-coder:free"
    assert result.source == "task_kind_models"
    assert "TASK_KIND_MODEL_SELECTED" in result.reason_codes


def test_select_rejects_mutation_task_kind() -> None:
    service = HermesModelSelectionService()
    result = service.select_model(config=_cfg(), context=HermesRoutingContext(task_kind="patch_apply"))
    assert result.selected_model is None
    assert result.source == "rejected"
    assert "MODEL_REJECTED_MUTATION_TASK" in result.reason_codes


def test_select_fallback_on_unavailable_model() -> None:
    service = HermesModelSelectionService()
    cfg = _cfg(task_kind_models={"plan_only": "bad/nonfree"})
    result = service.select_model(
        config=cfg,
        context=HermesRoutingContext(task_kind="plan_only", unavailable_models=["z-ai/glm-4.5-air:free"]),
    )
    assert result.selected_model == "moonshotai/kimi-k2:free"
    assert result.fallback_used is True
    assert "MODEL_REJECTED_NON_FREE" in result.reason_codes


def test_select_rejects_non_free_when_policy_requires_free() -> None:
    service = HermesModelSelectionService()
    cfg = _cfg(default_model="qwen/qwen3-coder", fallback_free_models={"default": []})
    result = service.select_model(config=cfg, context=HermesRoutingContext(task_kind="unknown_task_kind"))
    assert result.selected_model is None
    assert "MODEL_REJECTED_NON_FREE" in result.reason_codes
    assert "NO_MODEL_AVAILABLE" in result.reason_codes


def test_candidate_role_resolution_and_loader_fallback(tmp_path: Path) -> None:
    candidates = {
        "roles": {
            "lightweight_planner": [
                {
                    "model_id": "z-ai/glm-4.5-air:free",
                    "response_profile": {"preferred_format": "strict_json"},
                }
            ]
        }
    }
    candidate_path = tmp_path / "hermes_candidates.json"
    candidate_path.write_text(json.dumps(candidates), encoding="utf-8")
    service = HermesModelSelectionService()
    service._DEFAULT_CANDIDATE_PATH = candidate_path
    cfg = _cfg(task_kind_models={"plan_only": "role:lightweight_planner"})
    result = service.select_model(config=cfg, context=HermesRoutingContext(task_kind="plan_only"))
    assert result.selected_model == "z-ai/glm-4.5-air:free"
    assert result.source == "candidate_role"
    assert "CANDIDATE_ROLE_RESOLVED" in result.reason_codes
    assert result.selected_response_profile == {"preferred_format": "strict_json"}
