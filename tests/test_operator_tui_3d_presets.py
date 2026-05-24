from __future__ import annotations

import pytest

from client_surfaces.operator_tui.animation3d.presets import AnimationPreset, builtin_presets


class TestPresets:
    def test_three_presets_exist(self):
        assert len(builtin_presets) == 3
        assert "rotate_in" in builtin_presets
        assert "snake_orbit" in builtin_presets
        assert "depth_pulse" in builtin_presets

    def test_preset_has_required_fields(self):
        for name, preset in builtin_presets.items():
            assert preset.name == name
            assert preset.duration_ms > 0
            assert preset.fps > 0
            assert 0 < preset.rotation_speed <= 5.0
            assert preset.a_color
            assert preset.snake_color

    def test_scale_at_is_deterministic(self):
        preset = builtin_presets["rotate_in"]
        assert preset.scale_at(0.0) == pytest.approx(0.7)  # noqa: F821

    def test_different_presets_have_different_params(self):
        rt = builtin_presets["rotate_in"]
        so = builtin_presets["snake_orbit"]
        dp = builtin_presets["depth_pulse"]
        # At least one parameter differs
        params = [(p.rotation_speed, p.fps, p.duration_ms) for p in (rt, so, dp)]
        assert len(set(params)) >= 2
