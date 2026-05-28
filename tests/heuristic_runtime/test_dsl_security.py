"""Tests für DSL Security — Capability-Checks (T05.05)."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.dsl.security import (
    check_dsl_capabilities,
    CapabilityCheckResult,
    _SNAKE_FORBIDDEN_CAPABILITIES,
    _SNAKE_ALLOWED_CAPABILITIES,
)


def _dsl_with_caps(*caps):
    return {
        "dsl_version": "2.0",
        "safety": {"allowed_capabilities": list(caps)},
        "action": {"kind": "follow_artifact"},
        "provenance": {"created_by": "test", "rationale": "x"},
    }


class TestDslSecurity:
    def test_allowed_capabilities_pass(self):
        for cap in _SNAKE_ALLOWED_CAPABILITIES:
            result = check_dsl_capabilities(_dsl_with_caps(cap), domain="tui_snake")
            assert result.passed, f"Cap {cap} should be allowed: {result.violations}"

    def test_network_access_rejected(self):
        result = check_dsl_capabilities(_dsl_with_caps("network_access"), domain="tui_snake")
        assert not result.passed
        assert any("network_access" in v for v in result.violations)

    def test_secret_access_rejected(self):
        result = check_dsl_capabilities(_dsl_with_caps("secret_access"), domain="tui_snake")
        assert not result.passed
        assert any("secret_access" in v for v in result.violations)

    def test_file_write_rejected(self):
        result = check_dsl_capabilities(_dsl_with_caps("file_write"), domain="tui_snake")
        assert not result.passed
        assert any("file_write" in v for v in result.violations)

    def test_send_to_worker_rejected(self):
        result = check_dsl_capabilities(_dsl_with_caps("send_to_worker"), domain="tui_snake")
        assert not result.passed

    def test_request_context_extension_rejected(self):
        result = check_dsl_capabilities(_dsl_with_caps("request_context_extension"), domain="tui_snake")
        assert not result.passed

    def test_inline_code_rejected_as_capability(self):
        result = check_dsl_capabilities(_dsl_with_caps("inline_code"), domain="tui_snake")
        assert not result.passed

    def test_shell_command_rejected_as_capability(self):
        result = check_dsl_capabilities(_dsl_with_caps("shell_command"), domain="tui_snake")
        assert not result.passed

    def test_eclipse_snake_domain_also_restricted(self):
        result = check_dsl_capabilities(_dsl_with_caps("network_access"), domain="eclipse_snake")
        assert not result.passed

    def test_snake_eclipse_domain_also_restricted(self):
        result = check_dsl_capabilities(_dsl_with_caps("file_write"), domain="snake_eclipse")
        assert not result.passed

    def test_other_domain_passes_everything(self):
        result = check_dsl_capabilities(_dsl_with_caps("network_access"), domain="chat_codecompass")
        assert result.passed

    def test_no_capabilities_passes(self):
        result = check_dsl_capabilities(_dsl_with_caps(), domain="tui_snake")
        assert result.passed

    def test_forbidden_key_inline_code_in_dsl_body_rejected(self):
        dsl = _dsl_with_caps()
        dsl["inline_code"] = "os.system('rm -rf')"
        result = check_dsl_capabilities(dsl, domain="tui_snake")
        assert not result.passed
        assert any("inline_code" in v for v in result.violations)

    def test_forbidden_key_eval_in_nested_rejected(self):
        dsl = _dsl_with_caps()
        dsl["action"]["eval"] = "something"
        result = check_dsl_capabilities(dsl, domain="tui_snake")
        assert not result.passed

    def test_all_forbidden_caps_individually_rejected(self):
        for cap in _SNAKE_FORBIDDEN_CAPABILITIES:
            result = check_dsl_capabilities(_dsl_with_caps(cap), domain="tui_snake")
            assert not result.passed, f"Cap '{cap}' should be forbidden"

    def test_multiple_violations_reported(self):
        result = check_dsl_capabilities(
            _dsl_with_caps("network_access", "file_write"), domain="tui_snake"
        )
        assert not result.passed
        assert len(result.violations) >= 2
