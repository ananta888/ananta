from __future__ import annotations

from agent.services.tools.workspace_mutation_tools import repo_apply_patch, repo_write_file, workspace_diff


def _policy_cfg(mode: str = "block") -> dict:
    return {
        "generated_source_line_policy": {
            "enabled": True,
            "mode": mode,
            "categories": {
                "production_source": {
                    "warn_after_lines": 3,
                    "hard_max_lines": 5,
                    "new_over_hard_action": "block",
                    "cross_hard_action": "require_followup",
                    "existing_over_hard_growth_action": "block",
                    "existing_over_hard_shrink_action": "warn",
                    "warn_action": "warn",
                }
            },
        },
        "require_materialized_scope": False,
    }


def test_repo_write_file_blocks_new_large_source_and_rolls_back(tmp_path):
    result = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "agent/new_big.py", "mode": "create_only", "content": "x\n" * 6},
        tool_call_id="t1",
        config=_policy_cfg("block"),
    )

    assert result["status"] == "policy_blocked"
    assert result["data"]["source_line_policy_result"]["status"] == "blocked"
    assert not (tmp_path / "agent" / "new_big.py").exists()


def test_repo_write_file_warn_mode_keeps_large_source(tmp_path):
    result = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "agent/new_big.py", "mode": "create_only", "content": "x\n" * 6},
        tool_call_id="t1",
        config=_policy_cfg("warn"),
    )

    assert result["status"] == "ok"
    assert result["data"]["source_line_policy_result"]["status"] == "warning"
    assert (tmp_path / "agent" / "new_big.py").exists()


def test_repo_apply_patch_blocks_growth_and_restores_original(tmp_path):
    target = tmp_path / "agent" / "legacy.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("a\nb\nc\nd\ne\nf\n", encoding="utf-8")
    original = target.read_text(encoding="utf-8")
    diff = """@@ -1,6 +1,7 @@
 a
 b
 c
 d
 e
 f
+g
"""

    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={"target_path": "agent/legacy.py", "variant": "unified_diff", "unified_diff": diff},
        tool_call_id="t2",
        config=_policy_cfg("block"),
    )

    assert result["status"] == "policy_blocked"
    assert target.read_text(encoding="utf-8") == original


def test_workspace_diff_includes_source_line_policy_result(tmp_path):
    target = tmp_path / "agent" / "medium.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n" * 4, encoding="utf-8")

    result = workspace_diff(
        workspace_dir=str(tmp_path),
        arguments={},
        tool_call_id="diff1",
        config=_policy_cfg("block"),
    )

    assert result["status"] == "ok"
    assert "source_line_policy_result" in result["data"]
