"""Light‑weight controller package.

This package previously attempted to mirror the public attributes of the
legacy :mod:`controller.controller` module by importing it on package
initialisation.  Importing the legacy module, however, triggers database
initialisation which makes unit tests that simply need the bundled
blueprints fail when a database isn't available.  The eager import also
introduced a circular import when ``controller.controller`` itself tried to
import :mod:`src.controller.routes`.

To keep imports side‑effect free, the compatibility layer has been removed
and only the ``ControllerAgent`` is exported here.  The legacy module can
still be imported directly as ``controller.controller`` where required.
"""

from __future__ import annotations
# Controller-Modul
from .agent import ControllerAgent

__all__ = ["ControllerAgent"]

