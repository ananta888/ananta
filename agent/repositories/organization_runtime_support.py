"""Shared session and canonical-digest helpers of the Organization runtime SQL adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

SessionFactory = Callable[[], Session]
GROUNDING_REF = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")


def default_session() -> Session:
    from agent.database import engine

    return Session(engine)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "GROUNDING_REF",
    "SessionFactory",
    "canonical_json",
    "default_session",
]
