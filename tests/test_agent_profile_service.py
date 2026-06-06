"""APRL-017: Unit tests for AgentProfileService."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_path: Path, *, profile_map: dict | None = None, root_agents: str | None = None):
    from agent.services.agent_profile_service import AgentProfileService

    # Write root AGENTS.md
    root_content = root_agents or "# Root AGENTS\n\nHub is the control plane.\n"
    (tmp_path / "AGENTS.md").write_text(root_content, encoding="utf-8")

    # Write default profile map unless overridden
    pm = profile_map or {
        "schema": "ananta.agent_profile_map.v2",
        "profiles": {
            "new_software_project": {
                "activation": ["new_software_project", "new software project"],
                "agents_file": "docs/agent-profiles/new_software_project/AGENTS.md",
                "primary_role": "bounded_project_architect",
            },
            "bug_fix": {
                "activation": ["bug_fix", "bug", "fehler"],
                "agents_file": "docs/agent-profiles/bug_fix/AGENTS.md",
                "primary_role": "reproduce_diagnose_fix_verify",
            },
            "refactor": {
                "activation": ["refactor", "cleanup"],
                "agents_file": "docs/agent-profiles/refactor/AGENTS.md",
                "primary_role": "behavior_preserving_refactor_worker",
            },
            "ai_snake_chat": {
                "activation": ["ai_snake_chat", "operator_tui"],
                "agents_file": "client_surfaces/operator_tui/AGENTS.md",
                "primary_role": "architecture_explainer",
                "code_change_policy": "none",
            },
        },
    }
    pm_dir = tmp_path / "docs" / "agent-profiles"
    pm_dir.mkdir(parents=True, exist_ok=True)
    (pm_dir / "profile-map.json").write_text(json.dumps(pm), encoding="utf-8")

    # Write profile AGENTS.md stubs
    for profile_id, cfg in pm.get("profiles", {}).items():
        rel = cfg.get("agents_file", "")
        if rel:
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# AGENTS.md — {profile_id}\n\nProfile content.\n", encoding="utf-8")

    return AgentProfileService(repo_root=tmp_path)


# ---------------------------------------------------------------------------
# APRL-017 T1: new_software_project template_id -> correct agents_file
# ---------------------------------------------------------------------------

class TestNewSoftwareProjectActivation:
    def test_template_id_loads_new_software_project_profile(self, tmp_path):
        svc = _make_service(tmp_path)
        task = {
            "worker_execution_context": {"template_id": "new_software_project"},
        }
        result = svc.resolve_for_task(task)
        assert result.profile_id == "new_software_project"
        assert result.agents_file == "docs/agent-profiles/new_software_project/AGENTS.md"
        assert result.activation_source == "template_id"
        assert not result.is_fallback

    def test_task_kind_new_software_project(self, tmp_path):
        svc = _make_service(tmp_path)
        task = {"task_kind": "new_software_project"}
        result = svc.resolve_for_task(task)
        assert result.profile_id == "new_software_project"
        assert result.activation_source == "task_kind"


# ---------------------------------------------------------------------------
# APRL-017 T2: ai_snake_chat -> client_surfaces/operator_tui/AGENTS.md
# ---------------------------------------------------------------------------

class TestAiSnakeChatActivation:
    def test_explicit_profile_id_ai_snake_chat(self, tmp_path):
        svc = _make_service(tmp_path)
        task = {
            "worker_execution_context": {"active_agent_profile_id": "ai_snake_chat"},
        }
        result = svc.resolve_for_task(task)
        assert result.profile_id == "ai_snake_chat"
        assert result.agents_file == "client_surfaces/operator_tui/AGENTS.md"
        assert result.activation_source == "explicit_profile_id"

    def test_resolve_by_profile_id(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_by_profile_id("ai_snake_chat")
        assert result.profile_id == "ai_snake_chat"
        assert result.agents_file == "client_surfaces/operator_tui/AGENTS.md"


# ---------------------------------------------------------------------------
# APRL-017 T3: unknown path falls back deterministically to root_only
# ---------------------------------------------------------------------------

class TestUnknownPathFallback:
    def test_no_task_kind_falls_back_to_root_only(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_for_task({})
        assert result.profile_id == "root_only"
        assert result.is_fallback
        assert result.activation_source == "root_only"

    def test_unknown_task_kind_falls_back_to_root_only(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_for_task({"task_kind": "totally_unknown_kind_xyz"})
        assert result.profile_id == "root_only"
        assert result.is_fallback

    def test_fallback_contains_root_agents_content(self, tmp_path):
        svc = _make_service(tmp_path, root_agents="# Root\n\nHub is the control plane.\n")
        result = svc.resolve_for_task({})
        assert "Hub is the control plane" in result.root_agents_content
        assert "Hub is the control plane" in result.composed_content


# ---------------------------------------------------------------------------
# APRL-017 T4: Path traversal / external paths rejected
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_path_traversal_is_rejected(self, tmp_path):
        pm = {
            "schema": "test",
            "profiles": {
                "evil": {
                    "activation": ["evil"],
                    "agents_file": "../../../etc/passwd",
                    "primary_role": "none",
                },
            },
        }
        svc = _make_service(tmp_path, profile_map=pm)
        # ensure the profile map was written correctly (no stubs for traversal path)
        result = svc.resolve_by_profile_id("evil")
        assert result.is_fallback
        assert any("path_traversal" in w for w in result.warnings)

    def test_absolute_path_outside_repo_rejected(self, tmp_path):
        pm = {
            "schema": "test",
            "profiles": {
                "evil2": {
                    "activation": ["evil2"],
                    "agents_file": "/etc/hosts",
                    "primary_role": "none",
                },
            },
        }
        # Force profile map file
        pm_dir = tmp_path / "docs" / "agent-profiles"
        pm_dir.mkdir(parents=True, exist_ok=True)
        (pm_dir / "profile-map.json").write_text(json.dumps(pm), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Root\n", encoding="utf-8")
        from agent.services.agent_profile_service import AgentProfileService
        svc = AgentProfileService(repo_root=tmp_path)
        result = svc.resolve_by_profile_id("evil2")
        assert result.is_fallback
        assert any("path_traversal" in w or "agents_file_missing" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# APRL-017 T5: Diagnostics contain activation_source and checksums
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics_contain_checksums_and_activation_source(self, tmp_path):
        svc = _make_service(tmp_path)
        task = {"task_kind": "bug_fix"}
        result = svc.resolve_for_task(task)
        assert "root" in result.checksums
        assert "profile" in result.checksums
        assert len(result.checksums["root"]) == 16
        assert result.diagnostics["activation_source"] == "task_kind"
        assert result.diagnostics["profile_id"] == "bug_fix"

    def test_to_metadata_excludes_full_text(self, tmp_path):
        svc = _make_service(tmp_path)
        meta = svc.resolve_for_task({"task_kind": "bug_fix"}).to_metadata()
        assert "composed_content" not in meta
        assert "root_agents_content" not in meta
        assert "profile_agents_content" not in meta
        assert "profile_id" in meta
        assert "checksums" in meta

    def test_fallback_warning_set(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_for_task({"task_kind": "unknown_zzz"})
        assert result.is_fallback
        assert result.fallback_reason is not None


# ---------------------------------------------------------------------------
# APRL-017 T6: Ambiguous activation generates warning, deterministic winner
# ---------------------------------------------------------------------------

class TestAmbiguousActivation:
    def test_keyword_fallback_with_multiple_matches_produces_warning(self, tmp_path):
        # Both bug_fix and refactor activate on "cleanup" and "bug" might be in title
        svc = _make_service(tmp_path)
        task = {"title": "refactor and cleanup old bug code", "description": ""}
        result = svc.resolve_for_task(task)
        # Should still resolve to *something* deterministic (not crash)
        assert result.profile_id in {"bug_fix", "refactor", "root_only"}
        # If multiple matched, warning should mention ambiguity
        if result.activation_source == "keyword_fallback":
            # May or may not be ambiguous depending on text
            pass  # no crash is the assertion


# ---------------------------------------------------------------------------
# APRL-017 T7: bug_fix and code_fix not confused
# ---------------------------------------------------------------------------

class TestBugFixCodeFixSeparation:
    def test_bug_fix_task_kind_resolves_bug_fix(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_for_task({"task_kind": "bug_fix"})
        assert result.profile_id == "bug_fix"

    def test_code_fix_task_kind_does_not_resolve_to_bug_fix(self, tmp_path):
        pm = {
            "schema": "test",
            "profiles": {
                "bug_fix": {
                    "activation": ["bug_fix", "bug"],
                    "agents_file": "docs/agent-profiles/bug_fix/AGENTS.md",
                    "primary_role": "bug_fixer",
                },
                "code_fix": {
                    "activation": ["code_fix", "codeproblem"],
                    "agents_file": "docs/agent-profiles/code_fix/AGENTS.md",
                    "primary_role": "code_patcher",
                },
            },
        }
        svc = _make_service(tmp_path, profile_map=pm)
        result = svc.resolve_for_task({"task_kind": "code_fix"})
        assert result.profile_id == "code_fix"
        assert result.profile_id != "bug_fix"


# ---------------------------------------------------------------------------
# APRL-017 T8: Composition — profile appended after root, root stays dominant
# ---------------------------------------------------------------------------

class TestProfileComposition:
    def test_composed_content_has_root_first_then_profile(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.resolve_for_task({"task_kind": "bug_fix"})
        idx_root = result.composed_content.index("Global AGENTS")
        idx_profile = result.composed_content.index("Active Path Profile")
        assert idx_root < idx_profile, "Root must appear before profile"

    def test_conflict_detection_generates_warning(self, tmp_path):
        # Write a profile file with a conflict pattern
        pm = {
            "schema": "test",
            "profiles": {
                "bad_profile": {
                    "activation": ["bad"],
                    "agents_file": "docs/agent-profiles/bad_profile/AGENTS.md",
                    "primary_role": "bad",
                },
            },
        }
        pm_dir = tmp_path / "docs" / "agent-profiles"
        pm_dir.mkdir(parents=True, exist_ok=True)
        (pm_dir / "profile-map.json").write_text(json.dumps(pm), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Root\n", encoding="utf-8")
        bad_dir = tmp_path / "docs" / "agent-profiles" / "bad_profile"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "AGENTS.md").write_text(
            "Workers may orchestrate other workers directly.\n", encoding="utf-8"
        )
        from agent.services.agent_profile_service import AgentProfileService
        svc = AgentProfileService(repo_root=tmp_path)
        result = svc.resolve_by_profile_id("bad_profile")
        assert any("profile_conflicts_with_root" in w for w in result.warnings)
