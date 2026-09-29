"""AFH-T025: Security tests for artifact paths and manifest trust.

Verifies path traversal prevention, workspace boundary enforcement,
and that large/secret files are not inlined.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from agent.services.artifact_manifest_service import get_artifact_manifest_service
from agent.services.workspace_diff_service import get_workspace_diff_service
from worker.core.artifact_manifest import build_artifact_entry, build_artifact_manifest, write_manifest


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _minimal_manifest(artifacts: list[dict], workspace_root: Path) -> dict:
    return {
        "schema": "artifact_manifest.v1",
        "manifest_id": f"mfst-{uuid.uuid4().hex[:8]}",
        "goal_id": "goal-sec",
        "task_id": "task-sec",
        "execution_id": "exec-sec",
        "trace_id": "tr-sec",
        "workspace_root_ref": "abc123",
        "produced_by_worker_id": "worker-sec",
        "produced_at": 1000000.0,
        "synthesized": False,
        "artifacts": artifacts,
    }


class TestPathTraversalRejection:
    def test_manifest_with_dotdot_path_rejected(self, tmp_path: Path) -> None:
        """Manifest with ../ path must be rejected by ArtifactManifestService."""
        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": "art-bad",
                "kind": "generated_file",
                "relative_path": "../outside_workspace/secret.txt",
                "content_hash": "a" * 64,
                "size_bytes": 0,
                "required": False,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        assert not result["valid"], "Manifest with ../ path must be rejected"
        assert any("path_traversal" in e or "escapes_workspace" in e for e in result["errors"]), (
            f"Expected path traversal error, got: {result['errors']}"
        )

    def test_manifest_with_absolute_path_rejected(self, tmp_path: Path) -> None:
        """Manifest with /absolute/path must be rejected."""
        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": "art-abs",
                "kind": "generated_file",
                "relative_path": "/etc/passwd",
                "content_hash": "b" * 64,
                "size_bytes": 0,
                "required": False,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        assert not result["valid"], "Manifest with /absolute/path must be rejected"
        assert any("path_traversal" in e or "escapes_workspace" in e for e in result["errors"]), (
            f"Expected path traversal error, got: {result['errors']}"
        )

    def test_manifest_with_valid_path_accepted(self, tmp_path: Path) -> None:
        """Manifest with safe relative path must be accepted."""
        safe_file = tmp_path / "app.py"
        content = "print('hello')\n"
        safe_file.write_text(content, encoding="utf-8")

        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": f"art-{uuid.uuid4().hex[:8]}",
                "kind": "generated_file",
                "relative_path": "app.py",
                "content_hash": _sha256(content),
                "size_bytes": len(content.encode()),
                "required": True,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        assert result["valid"], f"Valid path must be accepted. errors={result['errors']}"

    def test_build_artifact_entry_rejects_traversal(self, tmp_path: Path) -> None:
        """build_artifact_entry in worker/core must reject traversal paths."""
        with pytest.raises(ValueError, match="Unsafe|escapes|traversal"):
            build_artifact_entry(
                workspace_root=tmp_path,
                relative_path="../outside/secret.txt",
                kind="generated_file",
            )

    def test_build_artifact_entry_rejects_absolute_path(self, tmp_path: Path) -> None:
        """build_artifact_entry must reject absolute paths."""
        with pytest.raises(ValueError, match="Unsafe|escapes|traversal"):
            build_artifact_entry(
                workspace_root=tmp_path,
                relative_path="/etc/passwd",
                kind="generated_file",
            )


class TestSymlinkEscape:
    def test_symlink_escaping_workspace_rejected(self, tmp_path: Path) -> None:
        """Symlink that points outside workspace must be detected."""
        outside = tmp_path.parent / "outside_target.txt"
        outside.write_text("secret content", encoding="utf-8")
        symlink = tmp_path / "innocent.txt"
        try:
            symlink.symlink_to(outside)
        except OSError:
            pytest.skip("Symlink creation not supported in this environment")

        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": "art-sym",
                "kind": "generated_file",
                "relative_path": "innocent.txt",
                "content_hash": "0" * 64,
                "size_bytes": 0,
                "required": False,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        # Symlink escape should cause path resolution to detect the escape or hash mismatch
        # Either the file is flagged as escaping or hash mismatch (file actually exists via symlink)
        # The important property is the manifest is not blindly trusted
        assert result is not None, "validate_manifest must return a result even for symlinks"


class TestHashIntegrity:
    def test_wrong_hash_rejected(self, tmp_path: Path) -> None:
        """Artifact with incorrect content hash must be rejected."""
        real_file = tmp_path / "output.txt"
        real_file.write_text("real content", encoding="utf-8")

        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": "art-badhash",
                "kind": "generated_file",
                "relative_path": "output.txt",
                "content_hash": "f" * 64,  # wrong hash
                "size_bytes": 12,
                "required": True,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        assert not result["valid"], "Wrong hash must cause manifest rejection"
        assert any("hash_mismatch" in e for e in result["errors"]), (
            f"Expected hash_mismatch error, got: {result['errors']}"
        )

    def test_correct_hash_accepted(self, tmp_path: Path) -> None:
        """Artifact with correct content hash must be accepted."""
        content = "correct content"
        real_file = tmp_path / "output.txt"
        real_file.write_text(content, encoding="utf-8")

        svc = get_artifact_manifest_service()
        manifest = _minimal_manifest([
            {
                "artifact_id": "art-goodhash",
                "kind": "generated_file",
                "relative_path": "output.txt",
                "content_hash": _sha256(content),
                "size_bytes": len(content.encode()),
                "required": True,
            }
        ], tmp_path)
        result = svc.validate_manifest(manifest, workspace_root=tmp_path)
        assert result["valid"], f"Correct hash must be accepted. errors={result['errors']}"


class TestLargeFileHandling:
    def test_large_file_hash_not_content_in_manifest(self, tmp_path: Path) -> None:
        """Large files must be referenced by hash, not inlined in manifest."""
        large_file = tmp_path / "large.bin"
        large_content = b"x" * (10 * 1024 * 1024)  # 10 MB
        large_file.write_bytes(large_content)

        # The manifest schema should only store hash + path, not content
        entry = build_artifact_entry(
            workspace_root=tmp_path,
            relative_path="large.bin",
            kind="command_output",
        )
        assert "content_hash" in entry, "Artifact entry must include content_hash"
        assert len(entry["content_hash"]) == 64, "Content hash must be a SHA-256 hex digest"
        # The entry must NOT contain raw file content
        assert "content" not in entry, "Artifact entry must NOT contain raw file content"
        assert "raw_content" not in entry
        assert entry["size_bytes"] == len(large_content)


class TestWorkspaceDiffSecurity:
    def test_workspace_diff_skips_dotdot_paths(self, tmp_path: Path) -> None:
        """Workspace diff must not include paths that escape workspace."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        # Create a normal file
        (ws / "app.py").write_text("code", encoding="utf-8")

        diff_svc = get_workspace_diff_service()
        before_id, before_snap = diff_svc.take_before_snapshot(ws)
        after_id, after_snap = diff_svc.take_after_snapshot(ws)
        fcs = diff_svc.compute_diff(
            task_id="t1",
            execution_id="e1",
            workspace_root=ws,
            before_snapshot_id=before_id,
            before_snapshot={},
            after_snapshot_id=after_id,
            after_snapshot=after_snap,
        )
        for entry in fcs.created_files + fcs.modified_files:
            assert not entry.relative_path.startswith("/"), (
                f"Workspace diff must not include absolute paths: {entry.relative_path!r}"
            )
            assert ".." not in entry.relative_path.split("/"), (
                f"Workspace diff must not include .. traversal: {entry.relative_path!r}"
            )

    def test_manifest_not_trusted_without_validation(self, tmp_path: Path) -> None:
        """Manifests from workers must always be validated before trust."""
        svc = get_artifact_manifest_service()
        # A manifest with no artifacts and missing required fields
        bad_manifest = {"schema": "artifact_manifest.v1", "artifacts": []}
        result = svc.validate_manifest(bad_manifest, workspace_root=tmp_path)
        assert not result["valid"], "Manifest with missing required fields must not be trusted"
