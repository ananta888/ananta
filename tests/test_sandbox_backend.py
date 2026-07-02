"""Tests for SandboxBackend, FakeSandbox, SandboxAuditService — COSMOS-005."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.sandbox_backend import (
    ExecResult,
    FakeSandbox,
    SandboxAuditService,
    SandboxBackend,
    SandboxConfig,
)


# ── FakeSandbox — lifecycle ───────────────────────────────────────────────────

def test_fake_sandbox_start_returns_id():
    sb = FakeSandbox()
    sid = sb.start(SandboxConfig())
    assert isinstance(sid, str)
    assert sid.startswith("fake-")


def test_fake_sandbox_exec_records_call():
    sb = FakeSandbox()
    sid = sb.start(SandboxConfig())
    sb.exec(sid, ["echo", "hello"])
    log = sb.get_exec_log()
    assert len(log) == 1
    assert log[0]["cmd"] == ["echo", "hello"]
    assert log[0]["sandbox_id"] == sid


def test_fake_sandbox_exec_unknown_raises():
    sb = FakeSandbox()
    with pytest.raises(KeyError, match="Unknown sandbox"):
        sb.exec("nonexistent-id", ["ls"])


def test_fake_sandbox_copy_in_registers_file():
    sb = FakeSandbox()
    sid = sb.start(SandboxConfig())
    sb.copy_in(sid, Path("/host/file.txt"), "/workspace/file.txt")
    assert sb._sandboxes[sid]["files"]["/workspace/file.txt"] == "/host/file.txt"


def test_fake_sandbox_stop_marks_stopped():
    sb = FakeSandbox()
    sid = sb.start(SandboxConfig())
    assert not sb._sandboxes[sid]["stopped"]
    sb.stop(sid)
    assert sb._sandboxes[sid]["stopped"]


def test_fake_sandbox_cleanup_removes():
    sb = FakeSandbox()
    sid = sb.start(SandboxConfig())
    sb.cleanup(sid)
    assert sid not in sb._sandboxes


# ── SandboxConfig defaults ────────────────────────────────────────────────────

def test_sandbox_config_default_network_none():
    config = SandboxConfig()
    assert config.network == "none"


# ── SandboxAuditService ───────────────────────────────────────────────────────

def test_audit_exec_has_cmd_hash():
    sb = FakeSandbox()
    audit = SandboxAuditService()
    sid = sb.start(SandboxConfig())
    result = sb.exec(sid, ["pytest", "--version"])
    record = audit.audit_exec(sandbox_id=sid, cmd=["pytest", "--version"], result=result)
    assert "cmd_hash" in record
    assert record["cmd_hash"] == result.cmd_hash
    assert record["exit_code"] == 0


def test_network_policy_valid_values():
    audit = SandboxAuditService()
    assert audit.check_network_policy(SandboxConfig(network="none")) is True
    assert audit.check_network_policy(SandboxConfig(network="restricted")) is True
    assert audit.check_network_policy(SandboxConfig(network="allowed")) is True
    assert audit.check_network_policy(SandboxConfig(network="open")) is False
    assert audit.check_network_policy(SandboxConfig(network="")) is False


# ── Protocol compliance ───────────────────────────────────────────────────────

def test_sandbox_backend_protocol_compliance():
    """FakeSandbox must satisfy the SandboxBackend protocol at runtime."""
    assert isinstance(FakeSandbox(), SandboxBackend)
