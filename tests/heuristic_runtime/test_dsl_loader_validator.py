"""Tests für DSL Loader + Validator (T05.01)."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.dsl.loader import DslLoader, DslLoadError
from agent.services.heuristic_runtime.dsl.validator import DslValidator, ValidationResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_definition(*, mode="dsl_v2", status="candidate", dsl=None):
    if dsl is None:
        dsl = _valid_dsl()
    return {
        "status": status,
        "runtime": {
            "mode": mode,
            "dsl_v2": {"dsl": dsl},
        },
    }


def _valid_dsl():
    return {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": "follow_artifact", "confidence": 0.8},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "test", "rationale": "test rationale"},
    }


# ── DslLoader tests ───────────────────────────────────────────────────────────

class TestDslLoader:
    def setup_method(self):
        self.loader = DslLoader()

    def test_load_valid_dsl_v2(self):
        dsl = self.loader.load_from_definition(_minimal_definition())
        assert dsl["dsl_version"] == "2.0"
        assert "action" in dsl

    def test_load_wrong_mode_raises(self):
        with pytest.raises(DslLoadError, match="mode="):
            self.loader.load_from_definition(_minimal_definition(mode="python"))

    def test_load_null_mode_raises(self):
        with pytest.raises(DslLoadError):
            self.loader.load_from_definition(_minimal_definition(mode=None))

    def test_load_forbidden_status_raises(self):
        with pytest.raises(DslLoadError, match="status="):
            self.loader.load_from_definition(_minimal_definition(status="rejected"))

    def test_load_archived_status_raises(self):
        with pytest.raises(DslLoadError):
            self.loader.load_from_definition(_minimal_definition(status="archived"))

    def test_load_active_status_ok(self):
        dsl = self.loader.load_from_definition(_minimal_definition(status="active"))
        assert dsl is not None

    def test_load_experimental_live_status_ok(self):
        dsl = self.loader.load_from_definition(_minimal_definition(status="experimental_live"))
        assert dsl is not None

    def test_load_missing_dsl_block_raises(self):
        defn = {
            "status": "candidate",
            "runtime": {"mode": "dsl_v2", "dsl_v2": {}},
        }
        with pytest.raises(DslLoadError, match="fehlt"):
            self.loader.load_from_definition(defn)

    def test_load_dsl_not_dict_raises(self):
        defn = {
            "status": "candidate",
            "runtime": {"mode": "dsl_v2", "dsl_v2": {"dsl": "string_not_dict"}},
        }
        with pytest.raises(DslLoadError, match="kein Objekt"):
            self.loader.load_from_definition(defn)

    def test_load_from_file_non_json_raises(self):
        with pytest.raises(DslLoadError, match="JSON"):
            self.loader.load_from_file("/tmp/test.yaml")


# ── DslValidator tests ────────────────────────────────────────────────────────

class TestDslValidator:
    def setup_method(self):
        self.validator = DslValidator()

    def test_valid_dsl_passes(self):
        result = self.validator.validate(_valid_dsl())
        assert result.passed
        assert result.errors == []

    def test_wrong_dsl_version_fails(self):
        dsl = _valid_dsl()
        dsl["dsl_version"] = "1.0"
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("dsl_version" in e for e in result.errors)

    def test_unknown_observe_source_fails(self):
        dsl = _valid_dsl()
        dsl["observe"] = {"sources": ["tui.snapshot", "network.http"]}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("sources" in e for e in result.errors)

    def test_unknown_action_kind_fails(self):
        dsl = _valid_dsl()
        dsl["action"] = {"kind": "hack_system"}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("action.kind" in e for e in result.errors)

    def test_bad_safety_class_fails(self):
        dsl = _valid_dsl()
        dsl["safety"] = {"safety_class": "elevated"}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("safety_class" in e for e in result.errors)

    def test_missing_provenance_created_by_fails(self):
        dsl = _valid_dsl()
        dsl["provenance"] = {"rationale": "x"}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("created_by" in e for e in result.errors)

    def test_missing_provenance_rationale_fails(self):
        dsl = _valid_dsl()
        dsl["provenance"] = {"created_by": "x"}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("rationale" in e for e in result.errors)

    def test_forbidden_key_inline_code_fails(self):
        dsl = _valid_dsl()
        dsl["inline_code"] = "os.system('rm -rf')"
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("inline_code" in e for e in result.errors)

    def test_forbidden_key_nested_fails(self):
        dsl = _valid_dsl()
        dsl["action"]["exec"] = "shell"
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("exec" in e for e in result.errors)

    def test_lease_ttl_too_high_fails(self):
        dsl = _valid_dsl()
        dsl["lease"] = {"ttl_seconds": 200}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert any("ttl_seconds" in e for e in result.errors)

    def test_lease_ttl_ok(self):
        dsl = _valid_dsl()
        dsl["lease"] = {"ttl_seconds": 60}
        result = self.validator.validate(dsl)
        assert result.passed

    def test_experiment_ttl_warning(self):
        dsl = _valid_dsl()
        dsl["experiment"] = {"max_ttl_seconds": 25}
        result = self.validator.validate(dsl)
        assert result.passed  # warning, kein error
        assert any("max_ttl_seconds" in w for w in result.warnings)

    def test_error_summary_concatenates(self):
        dsl = {}
        result = self.validator.validate(dsl)
        assert not result.passed
        assert ";" in result.error_summary or len(result.errors) >= 1

    def test_all_known_action_kinds_pass(self):
        for kind in ("suggest_target", "follow_artifact", "lurk_near", "smooth_follow",
                     "fast_target", "explain_target", "no_action"):
            dsl = _valid_dsl()
            dsl["action"]["kind"] = kind
            result = self.validator.validate(dsl)
            assert result.passed, f"kind={kind} should pass: {result.errors}"

    def test_all_known_sources_pass(self):
        for src in ("tui.snapshot", "tui.delta", "tui.semantic", "tui.mouse", "tui.focus", "tui.history"):
            dsl = _valid_dsl()
            dsl["observe"] = {"sources": [src]}
            result = self.validator.validate(dsl)
            assert result.passed, f"source={src} should pass: {result.errors}"
