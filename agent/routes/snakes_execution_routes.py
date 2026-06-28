"""Public compatibility facade for snake execution routes."""
from __future__ import annotations

import sys

from . import snakes_execution_handlers as _handlers

# Preserve the historical monkeypatch/import surface while keeping the public
# route module small. Both names deliberately resolve to the same module object.
sys.modules[__name__] = _handlers
