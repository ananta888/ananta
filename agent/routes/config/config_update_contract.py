"""Contract of the POST /config update pipeline.

Each normalization step receives the requested update (``new_cfg``) and a
:class:`ConfigUpdateContext`, returns the (possibly replaced) update and
rejects invalid input by raising :class:`ConfigUpdateRejected`. The route
turns a rejection into the unchanged error response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class ConfigUpdateRejected(Exception):
    """An invalid config update; carries the error response fields."""

    def __init__(self, message: str, *, code: int = 400, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


@dataclass
class ConfigUpdateContext:
    """State shared by the normalization steps of one update request."""

    current_cfg: dict
    actor: Callable[[], str]
    model_routing_migration_factory: Callable[..., Any]
    policy_revision_update: Any = None


ConfigUpdateStep = Callable[[dict, ConfigUpdateContext], dict]


def require_dict(value: Any, message: str) -> dict:
    """Return ``value`` when it is a dict, otherwise reject with ``message``."""

    if not isinstance(value, dict):
        raise ConfigUpdateRejected(message)
    return value
