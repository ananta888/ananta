from __future__ import annotations

import json
from pathlib import Path

from agent.services.config_graph_builder_service import ConfigGraphBuilderService
from agent.services.config_graph_patch_service import PatchOp
from agent.services.config_graph_persistence_service import (
    ConfigGraphPersistenceService,
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs/agent-profiles").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "docs/agent-profiles/profile-map.json").write_text(json.dumps({
        "profiles": {
            "review": {
                "agents_file": "",
                "primary_role": "reviewer",
                "activation": [],
                "allowed_task_kinds": ["review"],
                "code_change_policy": "via_hub_task_worker",
            }
        }
    }), encoding="utf-8")
    (tmp_path / "user.json").write_text(json.dumps({
        "path_ai_modes": [
            {
                "path_glob": "docs/**",
                "blocked_ai_modes": ["code_gen"],
                "allowed_ai_modes": [],
            }
        ]
    }), encoding="utf-8")
    return tmp_path


def test_set_data_on_agent_profile_persists_to_profile_map(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = ConfigGraphBuilderService(repo_root=root).build()
    result = ConfigGraphPersistenceService(repo_root=root).persist(
        graph,
        [
            PatchOp(
                op="set_data",
                target="agent_profile::review",
                data={
                    "primary_role": "security_reviewer",
                    "allowed_task_kinds": ["review", "audit"],
                },
            )
        ],
    )

    assert result.success is True
    profile_map = json.loads(
        (root / "docs/agent-profiles/profile-map.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        profile_map["profiles"]["review"]["primary_role"]
        == "security_reviewer"
    )
    assert profile_map["profiles"]["review"]["allowed_task_kinds"] == [
        "review",
        "audit",
    ]
    assert result.source_diffs
    assert result.rollback_artifact["sources"]


def test_set_data_on_path_rule_persists_to_user_json(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = ConfigGraphBuilderService(
        repo_root=root,
        user_config=json.loads((root / "user.json").read_text(encoding="utf-8")),
    ).build()
    result = ConfigGraphPersistenceService(repo_root=root).persist(graph, [
        PatchOp(
            op="set_data",
            target="path_rule::docs/**",
            data={"blocked_ai_modes": ["code_gen", "full_llm"]},
        )
    ])

    assert result.success is True
    user_json = json.loads((root / "user.json").read_text(encoding="utf-8"))
    assert user_json["path_ai_modes"][0]["blocked_ai_modes"] == ["code_gen", "full_llm"]


def test_readonly_instruction_layer_is_not_persistable(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = ConfigGraphBuilderService(repo_root=root).build()
    result = ConfigGraphPersistenceService(repo_root=root).persist(graph, [
        PatchOp(op="set_data", target="instruction_layer::root", data={"content": "x"})
    ])

    assert result.success is False
    assert "readonly" in result.errors[0]


def test_model_provider_persists_chat_backend(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = ConfigGraphBuilderService(
        repo_root=root,
        user_config=json.loads((root / "user.json").read_text(encoding="utf-8")),
    ).build()

    result = ConfigGraphPersistenceService(repo_root=root).persist(
        graph,
        [
            PatchOp(
                op="set_data",
                target="model_provider::lmstudio",
                data={"backend": "ollama"},
            )
        ],
    )

    assert result.success is True
    user_json = json.loads((root / "user.json").read_text(encoding="utf-8"))
    assert user_json["chat_backend"] == "ollama"


def test_embedding_provider_persists_user_config_block(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    user_cfg = json.loads((root / "user.json").read_text(encoding="utf-8"))
    user_cfg["embedding_provider"] = {"provider": "local_hash"}
    (root / "user.json").write_text(json.dumps(user_cfg), encoding="utf-8")
    graph = ConfigGraphBuilderService(repo_root=root, user_config=user_cfg).build()

    result = ConfigGraphPersistenceService(repo_root=root).persist(
        graph,
        [
            PatchOp(
                op="set_data",
                target="embedding_model::default",
                data={"provider": "ollama", "model": "nomic-embed-text"},
            )
        ],
    )

    assert result.success is True
    user_json = json.loads((root / "user.json").read_text(encoding="utf-8"))
    assert user_json["embedding_provider"]["provider"] == "ollama"
    assert user_json["embedding_provider"]["model"] == "nomic-embed-text"


def test_rollback_restores_previous_source_content(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    svc = ConfigGraphPersistenceService(repo_root=root)
    graph = ConfigGraphBuilderService(repo_root=root).build()
    result = svc.persist(
        graph,
        [
            PatchOp(
                op="set_data",
                target="agent_profile::review",
                data={"primary_role": "changed"},
            )
        ],
    )
    assert result.success is True

    rollback = svc.rollback(result.rollback_artifact)

    assert rollback.success is True
    profile_map = json.loads(
        (root / "docs/agent-profiles/profile-map.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile_map["profiles"]["review"]["primary_role"] == "reviewer"
