"""Pseudo-3D ASCII/ANSI logo animation for the Operator TUI splash."""

from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend, LogoAnimationBackend
from client_surfaces.operator_tui.animation3d.capabilities import AnimationCapability, detect_3d_capability
from client_surfaces.operator_tui.animation3d.presets import AnimationPreset, builtin_presets

__all__ = [
    "AnimationCapability",
    "AnimationPreset",
    "BuiltinBackend",
    "LogoAnimationBackend",
    "builtin_presets",
    "detect_3d_capability",
]
