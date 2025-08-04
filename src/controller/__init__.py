"""Task controller package and compatibility layer for the legacy controller.

The repository contains a ``controller/controller.py`` module used by
various tests. To avoid import conflicts, this package mirrors the public
attributes of that module so ``import controller`` continues to work
whether the legacy module or this package is picked up first on
``sys.path``.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
from types import ModuleType

from .agent import ControllerAgent

__all__ = ["ControllerAgent"]

# Expose symbols from the legacy controller if it exists
# Path to the legacy module at repository root
_root = Path(__file__).resolve().parents[2] / "controller" / "controller.py"
if _root.exists():
    spec = importlib.util.spec_from_file_location("_legacy_controller", _root)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    for name in dir(module):
        if not name.startswith("_"):
            globals()[name] = getattr(module, name)
            __all__.append(name)
