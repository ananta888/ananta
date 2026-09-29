from __future__ import annotations

import os
import pytest

from agent.services.workspace_path_validator import (
    REASON_INVALID_PATH,
    REASON_OUTSIDE_WORKSPACE,
    REASON_PATH_TRAVERSAL,
    REASON_SYMLINK_ESCAPE,
    REASON_OK,
    WorkspacePathValidator,
)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def validator(workspace):
    return WorkspacePathValidator(str(workspace))


# ── Happy path ────────────────────────────────────────────────────────────────

def test_absolute_path_inside_workspace(validator, workspace):
    target = workspace / "app.py"
    target.write_text("x")
    res = validator.validate(str(target))
    assert res.ok is True
    assert res.reason == REASON_OK
    assert res.resolved_path == str(target.resolve())


def test_relative_path_inside_workspace(validator, workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("x")
    res = validator.validate("src/main.py")
    assert res.ok is True
    assert res.resolved_path.endswith("main.py")


def test_nested_path_inside_workspace(validator, workspace):
    (workspace / "a" / "b" / "c").mkdir(parents=True)
    target = workspace / "a" / "b" / "c" / "file.txt"
    target.write_text("x")
    res = validator.validate(str(target))
    assert res.ok is True


def test_workspace_root_itself_passes(validator, workspace):
    res = validator.validate(str(workspace))
    assert res.ok is True


# ── Path traversal ────────────────────────────────────────────────────────────

def test_dotdot_traversal_rejected(validator, workspace):
    res = validator.validate(str(workspace / ".." / "evil.txt"))
    assert res.ok is False
    assert res.reason in (REASON_PATH_TRAVERSAL, REASON_OUTSIDE_WORKSPACE)


def test_dotdot_relative_traversal_rejected(validator):
    res = validator.validate("../../etc/passwd")
    assert res.ok is False
    assert res.reason in (REASON_PATH_TRAVERSAL, REASON_OUTSIDE_WORKSPACE)


def test_absolute_path_outside_workspace_rejected(validator, tmp_path):
    outside = tmp_path / "other" / "secret.txt"
    (tmp_path / "other").mkdir()
    outside.write_text("x")
    res = validator.validate(str(outside))
    assert res.ok is False
    assert res.reason == REASON_OUTSIDE_WORKSPACE


def test_root_path_rejected(validator):
    res = validator.validate("/etc/passwd")
    assert res.ok is False
    assert res.reason == REASON_OUTSIDE_WORKSPACE


# ── Empty / invalid ───────────────────────────────────────────────────────────

def test_empty_path_rejected(validator):
    assert validator.validate("").ok is False
    assert validator.validate("").reason == REASON_INVALID_PATH


def test_whitespace_only_path_rejected(validator):
    assert validator.validate("   ").ok is False


# ── Symlinks ──────────────────────────────────────────────────────────────────

def test_symlink_inside_workspace_passes(validator, workspace):
    real = workspace / "real.txt"
    real.write_text("x")
    link = workspace / "link.txt"
    link.symlink_to(real)
    res = validator.validate(str(link))
    assert res.ok is True


def test_symlink_escaping_workspace_rejected(validator, workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = workspace / "evil_link.txt"
    link.symlink_to(outside)
    res = validator.validate(str(link))
    assert res.ok is False
    assert res.reason == REASON_SYMLINK_ESCAPE


# ── Spaces and special characters ────────────────────────────────────────────

def test_path_with_spaces_passes(validator, workspace):
    target = workspace / "my file with spaces.txt"
    target.write_text("x")
    res = validator.validate(str(target))
    assert res.ok is True
    assert " " in res.resolved_path


def test_path_with_unicode_passes(validator, workspace):
    target = workspace / "datei_ä.txt"
    target.write_text("x")
    res = validator.validate(str(target))
    assert res.ok is True


# ── build_safe_argv ───────────────────────────────────────────────────────────

def test_build_safe_argv_returns_list(validator, workspace):
    target = workspace / "app.py"
    target.write_text("x")
    argv = validator.build_safe_argv(str(target))
    assert isinstance(argv, list)
    assert len(argv) == 1
    assert argv[0] == str(target.resolve())


def test_build_safe_argv_raises_for_invalid(validator):
    with pytest.raises(ValueError, match="Path validation failed"):
        validator.build_safe_argv("/etc/passwd")


# ── Constructor guard ─────────────────────────────────────────────────────────

def test_empty_workspace_root_raises():
    with pytest.raises(ValueError):
        WorkspacePathValidator("")


def test_whitespace_workspace_root_raises():
    with pytest.raises(ValueError):
        WorkspacePathValidator("   ")


# ── Prefix collision safety ───────────────────────────────────────────────────

def test_workspace_prefix_not_confused_with_sibling(tmp_path):
    ws = tmp_path / "workspace"
    sibling = tmp_path / "workspace-other"
    ws.mkdir()
    sibling.mkdir()
    (sibling / "evil.txt").write_text("x")
    validator = WorkspacePathValidator(str(ws))
    res = validator.validate(str(sibling / "evil.txt"))
    assert res.ok is False
