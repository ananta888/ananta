from __future__ import annotations

import json
from pathlib import Path

from agent.services.config_graph_builder_service import ConfigGraphBuilderService
from agent.services.config_graph_effective_resolver import EffectiveConfigResolver
from agent.services.config_graph_patch_service import PatchOp
from agent.services.config_graph_persistence_service import ConfigGraphPersistenceService
from agent.services.path_ai_mode_policy_service import PathAiModePolicyService


def test_config_graph_rtipm_roundtrip_persists_arrays_booleans_numbers_and_rolls_back(tmp_path: Path) -> None:
    (tmp_path / "docs/agent-profiles").mkdir(parents=True)
    (tmp_path / "agent/services").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    (tmp_path / "docs/agent-profiles/profile-map.json").write_text('{"profiles":{}}', encoding="utf-8")
    (tmp_path / "user.json").write_text(json.dumps({
        "path_ai_modes": [
            {"path_glob": "agent/**", "blocked_ai_modes": ["full_llm"]}
        ],
        "restricted_inference": {
            "models": [{"id": "mock", "engine": "mock", "tasks": ["candidate_rerank"]}]
        },
        "codecompass_ranking": {"restricted_inference_rerank_enabled": False},
    }), encoding="utf-8")

    cfg = json.loads((tmp_path / "user.json").read_text(encoding="utf-8"))
    graph = ConfigGraphBuilderService(repo_root=tmp_path, user_config=cfg).build()
    persistence = ConfigGraphPersistenceService(repo_root=tmp_path)
    result = persistence.persist(graph, [
        PatchOp(op="set_data", target="path_rule::agent/**", data={
            "allowed_ai_modes": ["codecompass_only", "restricted_transformer_inference"],
            "allowed_model_engines": ["mock"],
            "allow_free_text_generation": False,
            "max_input_chars": 42,
            "max_batch_size": 3,
        }),
        PatchOp(op="set_data", target="codecompass_ranking::default", data={
            "restricted_inference_rerank_enabled": True,
            "score_weights": {"transformer_rerank_score": 1.0},
        }),
    ])

    assert result.success
    updated = json.loads((tmp_path / "user.json").read_text(encoding="utf-8"))
    rule = updated["path_ai_modes"][0]
    assert rule["allowed_model_engines"] == ["mock"]
    assert rule["allow_free_text_generation"] is False
    assert rule["max_input_chars"] == 42
    assert updated["codecompass_ranking"]["restricted_inference_rerank_enabled"] is True

    effective_graph = ConfigGraphBuilderService(repo_root=tmp_path, user_config=updated).build()
    effective = EffectiveConfigResolver(effective_graph).resolve(surface="ai_snake_chat", path="agent/file.py")
    assert effective.allowed_model_engines == ["mock"]
    assert effective.effective_codecompass_ranking is not None

    policy = PathAiModePolicyService.from_config(updated).resolve("agent/file.py")
    assert sorted(policy.allowed_model_engines) == ["mock"]

    rollback = persistence.rollback(result.rollback_artifact)
    assert rollback.success
    restored = json.loads((tmp_path / "user.json").read_text(encoding="utf-8"))
    assert restored["path_ai_modes"][0]["blocked_ai_modes"] == ["full_llm"]
