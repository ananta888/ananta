"""Tests for proposal artifact types. FA-T004."""
from __future__ import annotations

import pytest

from worker.core.proposal_artifacts import (
    PlannerProposalArtifact,
    FileProposalArtifact,
    PatchProposalArtifact,
    AdvisoryProposalArtifact,
)


class TestPlannerProposalArtifact:
    def test_create_valid(self):
        a = PlannerProposalArtifact.create(
            task_id="t1", goal_id="g1", source_strategy="json_schema_llm",
            parse_status="parsed",
            parsed_items=[{"title": "Create app.py", "instructions": "Write Flask app"}],
        )
        assert a.artifact_id.startswith("ppa-")
        assert a.adoption_status == "pending"
        d = a.to_dict()
        assert d["schema"] == "planner_proposal_artifact.v1"
        assert len(d["parsed_items"]) == 1

    def test_invalid_parse_status_rejected(self):
        with pytest.raises(ValueError, match="invalid_parse_status"):
            PlannerProposalArtifact(
                artifact_id="x", task_id="t1", goal_id="g1",
                source_strategy="s", parse_status="bad_status",
            )

    def test_invalid_adoption_status_rejected(self):
        with pytest.raises(ValueError, match="invalid_adoption_status"):
            PlannerProposalArtifact(
                artifact_id="x", task_id="t1", goal_id="g1",
                source_strategy="s", adoption_status="approved",
            )


class TestFileProposalArtifact:
    def test_create_valid(self):
        a = FileProposalArtifact.create(
            task_id="t1", goal_id="g1", source_strategy="tool_calling_llm",
            relative_path="src/app.py",
            content_ref="sha256:abc123",
        )
        assert a.artifact_id.startswith("fpa-")
        assert a.operation == "create"
        d = a.to_dict()
        assert d["schema"] == "file_proposal_artifact.v1"
        assert d["relative_path"] == "src/app.py"

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="unsafe_proposal_artifact_path"):
            FileProposalArtifact.create(
                task_id="t1", goal_id="g1", source_strategy="s",
                relative_path="/etc/passwd",
                content_ref="ref",
            )

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="unsafe_proposal_artifact_path"):
            FileProposalArtifact.create(
                task_id="t1", goal_id="g1", source_strategy="s",
                relative_path="../secrets.txt",
                content_ref="ref",
            )

    def test_invalid_operation_rejected(self):
        with pytest.raises(ValueError, match="invalid_file_proposal_operation"):
            FileProposalArtifact(
                artifact_id="x", task_id="t1", goal_id="g1",
                source_strategy="s", relative_path="app.py",
                content_ref="ref", operation="execute",
            )

    def test_subdirectory_path_valid(self):
        a = FileProposalArtifact.create(
            task_id="t1", goal_id="g1", source_strategy="s",
            relative_path="tests/test_app.py", content_ref="ref",
        )
        assert a.relative_path == "tests/test_app.py"


class TestPatchProposalArtifact:
    def test_create_valid(self):
        a = PatchProposalArtifact.create(
            task_id="t1", goal_id="g1", source_strategy="worker_strategy",
            target_paths=["src/app.py", "tests/test_app.py"],
            patch_ref="sha256:def456",
        )
        assert a.artifact_id.startswith("ppa-patch-")
        d = a.to_dict()
        assert d["schema"] == "patch_proposal_artifact.v1"
        assert "src/app.py" in d["target_paths"]

    def test_absolute_target_path_rejected(self):
        with pytest.raises(ValueError, match="unsafe_proposal_artifact_path"):
            PatchProposalArtifact.create(
                task_id="t1", goal_id="g1", source_strategy="s",
                target_paths=["/etc/passwd"],
                patch_ref="ref",
            )

    def test_path_traversal_in_targets_rejected(self):
        with pytest.raises(ValueError, match="unsafe_proposal_artifact_path"):
            PatchProposalArtifact.create(
                task_id="t1", goal_id="g1", source_strategy="s",
                target_paths=["../../secrets"],
                patch_ref="ref",
            )


class TestAdvisoryProposalArtifact:
    def test_create_valid(self):
        a = AdvisoryProposalArtifact.create(
            task_id="t1", goal_id="g1", source_strategy="flexible_llm_normalization",
            text="Consider using FastAPI instead of Flask for async support.",
        )
        assert a.artifact_id.startswith("adv-")
        d = a.to_dict()
        assert d["schema"] == "advisory_proposal_artifact.v1"
        assert d["source_format"] == "natural_language"
        assert "FastAPI" in d["text"]
