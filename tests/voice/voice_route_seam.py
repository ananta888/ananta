"""Test helper: replace one Voice route collaborator through the dependency seam.

The Voice handlers resolve their collaborators via
``agent.routes.voice_route_dependencies.VOICE_ROUTE_DEPENDENCIES``. This helper
installs a ``MagicMock`` for one collaborator on the given Flask application for
the duration of a ``with`` block and yields it, so tests can configure and
assert on it like a ``unittest.mock.patch`` target.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock

from agent.routes.voice_route_dependencies import VOICE_ROUTE_DEPENDENCIES


@contextmanager
def override_voice_route(app: Any, name: str, **mock_kwargs: Any) -> Iterator[MagicMock]:
    """Override ``name`` on ``app``'s Voice route dependencies with a ``MagicMock``."""

    replacement = MagicMock(**mock_kwargs)
    with VOICE_ROUTE_DEPENDENCIES.override(app, **{name: replacement}):
        yield replacement
