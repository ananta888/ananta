"""AWWPI-009/010/018: mutation tools and workspace mutation policy tests."""
import hashlib
import json

from agent.services.ananta_workspace_mutation_policy import (
    get_ananta_workspace_mutation_policy_service,
)
from agent.services.tools.workspace_mutation_tools import (
    repo_apply_patch,
    repo_write_file,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- repo.apply_patch (AWWPI-009) --------------------------------------------


def _patch_for(old: str, new: str) -> str:
    return "\n".join(
        [
            "--- a/target.py",
            "+++ b/target.py",
            "@@ -1,2 +1,2 @@",
            f"-{old}",
            f"+{new}",
            " print('keep')",
        ]
    )


def test_apply_patch_applies_valid_unified_diff(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("value = 1\nprint('keep')\n", encoding="utf-8")
    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={
            "target_path": "target.py",
            "unified_diff": _patch_for("value = 1", "value = 2"),
            "expected_old_hash": _sha("value = 1\nprint('keep')\n"),
            "reason": "bump value",
        },
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "ok"
    assert result["data"]["applied"] is True
    assert result["data"]["changed_files"] == ["target.py"]
    assert target.read_text(encoding="utf-8") == "value = 2\nprint('keep')\n"


def test_apply_patch_rejects_hash_conflict_without_partial_change(tmp_path):
    target = tmp_path / "target.py"
    original = "value = 1\nprint('keep')\n"
    target.write_text(original, encoding="utf-8")
    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={
            "target_path": "target.py",
            "unified_diff": _patch_for("value = 1", "value = 2"),
            "expected_old_hash": "deadbeef",
        },
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "rejected"
    assert result["data"]["rejected_reason"] == "expected_old_hash_mismatch"
    assert target.read_text(encoding="utf-8") == original


def test_apply_patch_rejects_context_mismatch(tmp_path):
    target = tmp_path / "target.py"
    original = "totally = 'different'\ncontent = True\n"
    target.write_text(original, encoding="utf-8")
    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={"target_path": "target.py", "unified_diff": _patch_for("value = 1", "value = 2")},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "rejected"
    assert result["data"]["rejected_reason"] == "hunk_context_mismatch"
    assert target.read_text(encoding="utf-8") == original


def test_apply_patch_blocks_path_traversal(tmp_path):
    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={"target_path": "../outside.py", "unified_diff": _patch_for("a", "b")},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "rejected"
    assert "path_traversal" in result["data"]["rejected_reason"] or "outside" in result["data"]["rejected_reason"]


# --- repo.write_file (AWWPI-010) ----------------------------------------------


def test_write_file_create_only_succeeds_and_rejects_existing(tmp_path):
    result = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "new_module.py", "content": "x = 1\n", "mode": "create_only"},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "ok"
    assert (tmp_path / "new_module.py").read_text(encoding="utf-8") == "x = 1\n"

    second = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "new_module.py", "content": "x = 2\n", "mode": "create_only"},
        tool_call_id="tool_result:2",
    )
    assert second["status"] == "rejected"
    assert second["data"]["rejected_reason"] == "file_already_exists"


def test_write_file_replace_requires_hash_or_approval(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("old\n", encoding="utf-8")
    rejected = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "mod.py", "content": "new\n", "mode": "replace_existing"},
        tool_call_id="tool_result:1",
    )
    assert rejected["status"] == "rejected"
    assert rejected["data"]["rejected_reason"] == "replace_requires_expected_old_hash_or_approval"

    ok = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={
            "path": "mod.py",
            "content": "new\n",
            "mode": "replace_existing",
            "expected_old_hash": _sha("old\n"),
        },
        tool_call_id="tool_result:2",
    )
    assert ok["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_write_file_blocks_binary_replace_and_large_content(tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02")
    blocked = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "blob.bin", "content": "text", "mode": "replace_existing", "expected_old_hash": "x"},
        tool_call_id="tool_result:1",
    )
    assert blocked["data"]["rejected_reason"] == "binary_file_replace_blocked"

    too_large = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={"path": "big.txt", "content": "a" * 5000, "mode": "create_only"},
        tool_call_id="tool_result:2",
        config={"max_write_file_bytes": 1024},
    )
    assert too_large["data"]["rejected_reason"] == "content_too_large"


# --- mutation policy (AWWPI-004/008) -------------------------------------------


def _policy():
    return get_ananta_workspace_mutation_policy_service()


def test_policy_allows_materialized_file_and_blocks_unlisted(tmp_path):
    (tmp_path / "allowed.py").write_text("ok\n", encoding="utf-8")
    (tmp_path / "unlisted.py").write_text("nope\n", encoding="utf-8")
    manifest = [{"workspace_path": "allowed.py", "allowed_operations": ["read", "write"]}]
    result = _policy().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["allowed.py", "unlisted.py"],
        materialization_manifest=manifest,
    )
    assert result.status == "policy_violation"
    assert result.allowed_changes == ["allowed.py"]
    assert [row["path"] for row in result.blocked_changes] == ["unlisted.py"]


def test_policy_blocks_forbidden_paths_and_secrets(tmp_path):
    for rel in [".git/config", ".ananta/task-brief.md", "rag_helper/x.md", ".env", "deploy/id_rsa"]:
        result = _policy().evaluate_changed_files(
            workspace_dir=tmp_path, changed_rel_paths=[rel], materialization_manifest=[]
        )
        assert result.status == "policy_violation", rel


def test_policy_blocks_traversal_and_absolute_paths(tmp_path):
    result = _policy().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["../escape.py", "/etc/passwd"],
        materialization_manifest=[],
    )
    reasons = {row["reason"] for row in result.blocked_changes}
    assert "path_traversal_blocked" in reasons
    assert "absolute_path_blocked" in reasons


def test_policy_blocks_deleted_files_pending_approval(tmp_path):
    manifest = [{"workspace_path": "gone.py", "allowed_operations": ["write"]}]
    result = _policy().evaluate_changed_files(
        workspace_dir=tmp_path, changed_rel_paths=["gone.py"], materialization_manifest=manifest
    )
    assert result.blocked_changes[0]["reason"] == "delete_or_rename_requires_separate_approval"


def test_policy_strict_marker_escalates(tmp_path):
    (tmp_path / "deployment").mkdir()
    (tmp_path / "deployment" / "app.yaml").write_text("x\n", encoding="utf-8")
    manifest = [{"workspace_path": "deployment/app.yaml", "allowed_operations": ["write"]}]
    result = _policy().evaluate_changed_files(
        workspace_dir=tmp_path, changed_rel_paths=["deployment/app.yaml"], materialization_manifest=manifest
    )
    assert result.escalate_to_strict is True


def test_policy_allows_new_files_via_glob(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("def test(): pass\n", encoding="utf-8")
    result = _policy().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["tests/test_new.py"],
        materialization_manifest=[],
        allowed_new_file_globs=["tests/test_*.py"],
    )
    assert result.status == "ok"
    assert result.allowed_changes == ["tests/test_new.py"]


# --- mutation mode resolution (AWWPI-002/013) -----------------------------------


def test_resolve_mutation_mode_explicit_and_mapping_and_fallback():
    policy = _policy()
    cfg = {
        "mode_by_task_kind": {"coding": "controlled_workspace", "analysis": "read_only"},
        "escalate_to_strict_risks": ["high", "critical"],
    }
    assert policy.resolve_mutation_mode(cfg=cfg, explicit_mode="strict_patch_request") == "strict_patch_request"
    assert policy.resolve_mutation_mode(cfg=cfg, task_kind="coding") == "controlled_workspace"
    assert policy.resolve_mutation_mode(cfg=cfg, task_kind="analysis") == "read_only"
    assert policy.resolve_mutation_mode(cfg=cfg, task_kind="unknown_kind") == "read_only"
    assert policy.resolve_mutation_mode(cfg=cfg, explicit_mode="bogus_mode") == "read_only"


def test_resolve_mutation_mode_risk_escalates_to_strict():
    policy = _policy()
    cfg = {"mode_by_task_kind": {"coding": "controlled_workspace"}, "escalate_to_strict_risks": ["high", "critical"]}
    assert policy.resolve_mutation_mode(cfg=cfg, task_kind="coding", risk="high") == "strict_patch_request"
    assert policy.resolve_mutation_mode(cfg=cfg, task_kind="coding", risk="low") == "controlled_workspace"
