"""Lazy DSPy import and version capability boundary."""

from __future__ import annotations

import importlib
from typing import Any

from worker.optimization.dspy.runtime_security import DspyRuntimeSecurityPolicy


class DspyCompatibilityAdapter:
    SUPPORTED_VERSION = "3.2.1"

    def inspect(self) -> dict[str, Any]:
        try:
            module = importlib.import_module("dspy")
        except ImportError:
            return self._projection("unavailable", None, "dspy_dependency_unavailable")
        try:
            DspyRuntimeSecurityPolicy.apply(module)
        except RuntimeError:
            return self._projection("degraded", None, "dspy_secure_cache_configuration_unavailable")
        version = str(getattr(module, "__version__", ""))
        if version != self.SUPPORTED_VERSION:
            return self._projection("degraded", version or None, "dspy_version_incompatible")
        required = ("Predict", "ChainOfThought", "LabeledFewShot", "BootstrapFewShot")
        if any(not hasattr(module, name) for name in required):
            return self._projection("degraded", version, "dspy_capability_missing")
        return self._projection("available", version, "dspy_compatible")

    @staticmethod
    def _projection(state: str, version: str | None, reason: str) -> dict[str, Any]:
        return {
            "state": state,
            "installed_version": version,
            "compatibility_profile": "dspy-3.2.1-ananta-v1",
            "reason_code": reason,
            "network_probe_performed": False,
        }


__all__ = ["DspyCompatibilityAdapter"]
