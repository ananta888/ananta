"""Tests for feature_flags.py (EW-T062)."""
import pytest
from worker.core.feature_flags import (
    DEFAULT_FLAGS,
    KNOWN_FLAGS,
    FeatureFlag,
    FeatureFlagRegistry,
    MigrationStage,
    build_default_registry,
)


class TestFeatureFlag:
    def test_known_flag_creates_ok(self):
        flag = FeatureFlag(
            name="require_execution_envelope",
            default_enabled=True,
            description="Test flag",
        )
        assert flag.name == "require_execution_envelope"

    def test_unknown_flag_rejected(self):
        with pytest.raises(ValueError, match="Unknown flag"):
            FeatureFlag(name="god_mode_flag", default_enabled=True, description="x")

    def test_rollout_percentage_bounds(self):
        with pytest.raises(ValueError, match="rollout_percentage"):
            FeatureFlag(
                name="require_execution_envelope",
                default_enabled=True,
                description="x",
                rollout_percentage=101,
            )
        with pytest.raises(ValueError):
            FeatureFlag(
                name="require_execution_envelope",
                default_enabled=True,
                description="x",
                rollout_percentage=-1,
            )

    def test_valid_rollout_percentages(self):
        for pct in (0, 50, 100):
            f = FeatureFlag(
                name="require_execution_envelope",
                default_enabled=True,
                description="x",
                rollout_percentage=pct,
            )
            assert f.rollout_percentage == pct

    def test_frozen(self):
        flag = FeatureFlag(
            name="require_execution_envelope",
            default_enabled=True,
            description="x",
        )
        with pytest.raises((TypeError, AttributeError)):
            flag.default_enabled = False  # type: ignore


class TestFeatureFlagRegistry:
    def setup_method(self):
        self.registry = build_default_registry()

    def test_unknown_flag_returns_false(self):
        assert self.registry.is_enabled("nonexistent_flag") is False

    def test_default_flag_state(self):
        for flag in DEFAULT_FLAGS:
            result = self.registry.is_enabled(flag.name)
            assert result == flag.default_enabled

    def test_hub_config_overrides_defaults(self):
        self.registry.apply_hub_config({"require_execution_envelope": False})
        assert self.registry.is_enabled("require_execution_envelope") is False

    def test_hub_config_unknown_keys_ignored(self):
        # Should not raise, should not affect known flags
        self.registry.apply_hub_config({"totally_unknown_flag": True})
        # No exception, and unknown flag stays False
        assert self.registry.is_enabled("totally_unknown_flag") is False

    def test_hub_config_only_known_flags_applied(self):
        original = self.registry.is_enabled("enable_skill_system")
        self.registry.apply_hub_config({
            "enable_skill_system": not original,
            "__evil_injection__": True,
        })
        assert self.registry.is_enabled("enable_skill_system") is not original
        assert self.registry.is_enabled("__evil_injection__") is False

    def test_snapshot_contains_all_flags(self):
        snap = self.registry.snapshot()
        for flag in DEFAULT_FLAGS:
            assert flag.name in snap

    def test_snapshot_values_are_bool(self):
        snap = self.registry.snapshot()
        for name, value in snap.items():
            assert isinstance(value, bool), f"{name} has non-bool value {value!r}"

    def test_migration_stage_governed(self):
        self.registry.apply_hub_config({"require_execution_envelope": True})
        assert self.registry.migration_stage() == MigrationStage.governed

    def test_migration_stage_compatibility(self):
        self.registry.apply_hub_config({
            "require_execution_envelope": False,
            "legacy_envelope_adapter_allowed": True,
        })
        assert self.registry.migration_stage() == MigrationStage.compatibility

    def test_migration_stage_legacy(self):
        self.registry.apply_hub_config({
            "require_execution_envelope": False,
            "legacy_envelope_adapter_allowed": False,
        })
        assert self.registry.migration_stage() == MigrationStage.legacy

    def test_flags_for_stage(self):
        governed_flags = self.registry.flags_for_stage(MigrationStage.governed)
        assert len(governed_flags) > 0
        for f in governed_flags:
            assert f.migration_stage == MigrationStage.governed

    def test_flags_for_compatibility_stage(self):
        compat_flags = self.registry.flags_for_stage(MigrationStage.compatibility)
        assert any(f.name == "legacy_envelope_adapter_allowed" for f in compat_flags)

    def test_security_flags_enabled_by_default(self):
        security_flags = [
            "require_execution_envelope",
            "require_capability_snapshot",
            "require_artifact_first",
            "enable_context_scanner",
            "enable_adapter_trust_boundary",
            "enable_audit_emitter",
            "block_cloud_by_default",
        ]
        for flag_name in security_flags:
            assert self.registry.is_enabled(flag_name), (
                f"Security flag {flag_name!r} should be enabled by default"
            )

    def test_opt_in_flags_disabled_by_default(self):
        opt_in_flags = [
            "enable_scheduled_jobs",
            "enable_api_exposure",
            "strict_tool_policy",
        ]
        for flag_name in opt_in_flags:
            assert not self.registry.is_enabled(flag_name), (
                f"Opt-in flag {flag_name!r} should be disabled by default"
            )


class TestKnownFlagsCompleteness:
    def test_all_default_flags_are_known(self):
        for flag in DEFAULT_FLAGS:
            assert flag.name in KNOWN_FLAGS, f"{flag.name!r} in DEFAULT_FLAGS but not KNOWN_FLAGS"

    def test_no_duplicates_in_default_flags(self):
        names = [f.name for f in DEFAULT_FLAGS]
        assert len(names) == len(set(names)), "Duplicate flag names in DEFAULT_FLAGS"

    def test_build_default_registry_returns_registry(self):
        r = build_default_registry()
        assert isinstance(r, FeatureFlagRegistry)
