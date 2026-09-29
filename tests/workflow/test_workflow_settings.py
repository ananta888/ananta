"""WFG-003: tests for the workflow-layer settings module."""
from __future__ import annotations

import os
import pytest

from agent.services.workflow_settings import (
    GateFailurePolicy,
    WorkflowMode,
    WorkflowSettings,
    get_workflow_settings,
    reset_workflow_settings_cache,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no ANANTA_WORKFLOW_* env vars leak between tests."""
    for key in list(os.environ):
        if key.startswith("ANANTA_WORKFLOW_"):
            monkeypatch.delenv(key, raising=False)
    reset_workflow_settings_cache()
    yield
    reset_workflow_settings_cache()


def test_defaults_when_no_env() -> None:
    s = WorkflowSettings.from_env()
    assert s.mode is WorkflowMode.AUTO
    assert s.default_gate_policy is GateFailurePolicy.BLOCK
    assert s.gate_timeout_seconds == 86400
    assert s.audit_enabled is True
    assert s.artifact_flow_enforced is True


def test_mode_off_disables_workflow_block() -> None:
    os.environ["ANANTA_WORKFLOW_MODE"] = "off"
    s = WorkflowSettings.from_env()
    assert s.mode is WorkflowMode.OFF
    assert s.workflow_block_respected() is False


def test_mode_auto_respects_workflow_block() -> None:
    os.environ["ANANTA_WORKFLOW_MODE"] = "auto"
    s = WorkflowSettings.from_env()
    assert s.workflow_block_respected() is True


def test_mode_enforce_respects_workflow_block() -> None:
    os.environ["ANANTA_WORKFLOW_MODE"] = "ENFORCE"  # case-insensitive
    s = WorkflowSettings.from_env()
    assert s.mode is WorkflowMode.ENFORCE
    assert s.workflow_block_respected() is True


def test_invalid_mode_raises() -> None:
    os.environ["ANANTA_WORKFLOW_MODE"] = "yolo"
    with pytest.raises(ValueError, match="ANANTA_WORKFLOW_MODE"):
        WorkflowSettings.from_env()


def test_invalid_default_gate_raises() -> None:
    os.environ["ANANTA_WORKFLOW_DEFAULT_GATE"] = "panic"
    with pytest.raises(ValueError, match="ANANTA_WORKFLOW_DEFAULT_GATE"):
        WorkflowSettings.from_env()


def test_invalid_gate_timeout_raises() -> None:
    os.environ["ANANTA_WORKFLOW_GATE_TIMEOUT"] = "abc"
    with pytest.raises(ValueError, match="ANANTA_WORKFLOW_GATE_TIMEOUT"):
        WorkflowSettings.from_env()


def test_negative_gate_timeout_raises() -> None:
    os.environ["ANANTA_WORKFLOW_GATE_TIMEOUT"] = "-5"
    with pytest.raises(ValueError, match=">= 0"):
        WorkflowSettings.from_env()


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("0", False), ("true", True), ("false", False),
    ("yes", True), ("no", False), ("on", True), ("off", False),
    ("TRUE", True), ("Yes", True),
])
def test_audit_enabled_accepts_common_truthy_strings(raw: str, expected: bool) -> None:
    os.environ["ANANTA_WORKFLOW_AUDIT_ENABLED"] = raw
    s = WorkflowSettings.from_env()
    assert s.audit_enabled is expected


def test_audit_enabled_invalid_string_raises() -> None:
    os.environ["ANANTA_WORKFLOW_AUDIT_ENABLED"] = "maybe"
    with pytest.raises(ValueError, match="ANANTA_WORKFLOW_AUDIT_ENABLED"):
        WorkflowSettings.from_env()


def test_get_workflow_settings_caches_until_reload() -> None:
    s1 = get_workflow_settings()
    s2 = get_workflow_settings()
    assert s1 is s2  # cached

    os.environ["ANANTA_WORKFLOW_MODE"] = "off"
    s3 = get_workflow_settings()  # still cached
    assert s3.mode is WorkflowMode.AUTO

    s4 = get_workflow_settings(force_reload=True)
    assert s4.mode is WorkflowMode.OFF


def test_reset_workflow_settings_cache_drops_cache() -> None:
    get_workflow_settings()
    reset_workflow_settings_cache()
    os.environ["ANANTA_WORKFLOW_MODE"] = "off"
    s = get_workflow_settings()
    assert s.mode is WorkflowMode.OFF


def test_with_overrides_returns_new_instance() -> None:
    base = WorkflowSettings.from_env()
    override = base.with_overrides(gate_timeout_seconds=42)
    assert base.gate_timeout_seconds == 86400
    assert override.gate_timeout_seconds == 42
    # base is unchanged (frozen dataclass)
    assert base is not override
