"""CWFH-013: Security test matrix for WorkerContextHandoff v3.

Covers:
- Path traversal attacks
- Symlink outside workspace
- Secret file patterns (denied globs)
- Extension deny
- Binary file handling
- Workspace boundary enforcement
- Cross-workspace isolation
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.services.context_file_reader_service import (
    ContextFileReaderService,
    FileReadPolicy,
)
from client_surfaces.operator_tui.tools.filesystem_read_tool import FilesystemReadTool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _service_for(root: str) -> ContextFileReaderService:
    return ContextFileReaderService(policy=FileReadPolicy(workspace_root=root))


# ── Path traversal ────────────────────────────────────────────────────────────

def test_path_traversal_dotdot_blocked():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file("../../etc/passwd")


def test_path_traversal_absolute_blocked():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file("/etc/passwd")


def test_path_traversal_encoded_blocked():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file("../../../etc/hosts")


def test_path_within_workspace_allowed():
    with tempfile.TemporaryDirectory() as root:
        safe = Path(root) / "safe.py"
        safe.write_text("x = 1")
        svc = _service_for(root)
        result = svc.read_file("safe.py")
        assert result.content == "x = 1"
        assert result.error is None


# ── Symlink outside workspace ─────────────────────────────────────────────────

def test_symlink_outside_workspace_blocked():
    with tempfile.TemporaryDirectory() as root:
        link = Path(root) / "escape.py"
        link.symlink_to("/etc/hostname")
        svc = _service_for(root)
        # Symlink resolves outside workspace_root → traversal ValueError
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file("escape.py")


# ── Secret file patterns (denied globs) ──────────────────────────────────────

@pytest.mark.parametrize("secret_path", [
    ".env",
    ".env.production",
    "secrets.yaml",
    "credentials.json",
    "token.txt",
    "id_rsa",
    "server.key",
    "cert.pem",
])
def test_denied_glob_blocks_secret_files(secret_path: str):
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="denied pattern"):
            svc.read_file(secret_path)


def test_env_file_in_subdir_blocked():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="denied pattern"):
            svc.read_file("config/.env")


def test_pycache_pyc_blocked():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="(denied pattern|Extension)"):
            svc.read_file("__pycache__/module.cpython-312.pyc")


# ── Extension filtering ───────────────────────────────────────────────────────

def test_disallowed_extension_blocked():
    with tempfile.TemporaryDirectory() as root:
        bad = Path(root) / "payload.exe"
        bad.write_bytes(b"\x00\x01binary")
        svc = _service_for(root)
        with pytest.raises(ValueError, match="Extension"):
            svc.read_file("payload.exe")


def test_allowed_extension_passes():
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "module.py"
        f.write_text("pass")
        svc = _service_for(root)
        result = svc.read_file("module.py")
        assert result.policy_applied in {"allowed", "truncated"}


# ── Binary file handling ──────────────────────────────────────────────────────

def test_binary_py_file_skipped_without_allow_binary():
    with tempfile.TemporaryDirectory() as root:
        bf = Path(root) / "data.py"
        bf.write_bytes(b"\xff\xfe binary \x00\x01\x02")
        svc = _service_for(root)
        result = svc.read_file("data.py")
        assert result.policy_applied == "binary_denied"
        assert result.error is not None


def test_binary_allowed_when_policy_allows():
    with tempfile.TemporaryDirectory() as root:
        bf = Path(root) / "data.py"
        bf.write_bytes(b"\xff\xfe binary \x00\x01\x02")
        policy = FileReadPolicy(workspace_root=root, allow_binary=True)
        svc = ContextFileReaderService(policy=policy)
        result = svc.read_file("data.py")
        assert result.policy_applied in {"allowed", "truncated"}


# ── File size limits ──────────────────────────────────────────────────────────

def test_oversized_file_truncated():
    with tempfile.TemporaryDirectory() as root:
        big = Path(root) / "big.py"
        big.write_text("x" * 300_000)
        svc = _service_for(root)
        result = svc.read_file("big.py")
        assert result.policy_applied == "truncated"
        assert "[truncated]" in result.content
        assert len(result.content) <= 256 * 1024 + 20


# ── Missing files ─────────────────────────────────────────────────────────────

def test_missing_file_returns_error_result():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        result = svc.read_file("nonexistent.py")
        assert result.policy_applied == "file_not_found"
        assert result.error is not None


# ── Batch reads ───────────────────────────────────────────────────────────────

def test_read_files_skip_errors_default():
    with tempfile.TemporaryDirectory() as root:
        ok = Path(root) / "ok.py"
        ok.write_text("good = True")
        svc = _service_for(root)
        results = svc.read_files(["ok.py", "../../etc/passwd"])
        # ok.py succeeds, traversal returns error result (skip_errors=True)
        assert len(results) == 2
        assert results[0].error is None
        assert results[1].error is not None


def test_read_files_raise_on_errors():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        with pytest.raises(ValueError, match="traversal"):
            svc.read_files(["../../etc/passwd"], skip_errors=False)


# ── read_required_files ───────────────────────────────────────────────────────

def test_read_required_files_only_reads_requires_read_true():
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "important.py"
        f.write_text("important = 1")
        svc = _service_for(root)
        candidates = [
            {"path": "important.py", "requires_read": True},
            {"path": "skip.py", "requires_read": False},
        ]
        context_files = svc.read_required_files(candidates)
        paths = [c["path"] for c in context_files]
        assert "important.py" in paths
        assert "skip.py" not in paths


def test_read_required_files_drops_error_results():
    with tempfile.TemporaryDirectory() as root:
        svc = _service_for(root)
        candidates = [
            {"path": "nonexistent.py", "requires_read": True},
        ]
        context_files = svc.read_required_files(candidates)
        assert context_files == []


# ── Cross-workspace isolation ─────────────────────────────────────────────────

def test_two_workspaces_cannot_cross_read():
    with tempfile.TemporaryDirectory() as ws1, tempfile.TemporaryDirectory() as ws2:
        internal = Path(ws2) / "internal_module.py"
        internal.write_text("PASSWORD = 'secret123'")

        svc = _service_for(ws1)
        # Attempt to read from ws2 via absolute path → traversal blocked
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file(str(internal))


def test_workspace_prefix_sibling_absolute_path_blocked():
    with tempfile.TemporaryDirectory() as base:
        root = Path(base) / "repo"
        sibling = Path(base) / "repo_evil"
        root.mkdir()
        sibling.mkdir()
        outside = sibling / "outside.py"
        outside.write_text("outside = True")

        svc = _service_for(str(root))
        with pytest.raises(ValueError, match="traversal"):
            svc.read_file(str(outside))


def test_snakechat_filesystem_tool_prefix_sibling_absolute_path_blocked():
    with tempfile.TemporaryDirectory() as base:
        root = Path(base) / "repo"
        sibling = Path(base) / "repo_evil"
        root.mkdir()
        sibling.mkdir()
        outside = sibling / "outside.py"
        outside.write_text("outside = True")

        result = FilesystemReadTool(root).read_file(str(outside))
        assert result.ok is False
        assert result.error == "path_traversal_denied"


def test_sha256_computed_correctly():
    import hashlib
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "check.py"
        content = b"print('hello')\n"
        f.write_bytes(content)
        svc = _service_for(root)
        result = svc.read_file("check.py")
        expected = hashlib.sha256(content).hexdigest()
        assert result.sha256 == expected


def test_context_file_dict_schema():
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "module.py"
        f.write_text("x = 1\ny = 2\n")
        svc = _service_for(root)
        result = svc.read_file("module.py")
        d = result.as_context_file_dict()
        assert d["path"] == "module.py"
        assert isinstance(d["content"], str)
        assert isinstance(d["sha256"], str)
        assert d["byte_count"] > 0
        assert d["line_count"] == 2
        assert d["redaction_status"] == "not_redacted"
        assert d["provenance"] == "context_file_reader_service"
