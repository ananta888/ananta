from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


DegradedState = Literal[
    "healthy",
    "backend_unreachable",
    "auth_failed",
    "capability_missing",
    "policy_denied",
    "malformed_response",
    "unknown_error",
]


@dataclass(frozen=True)
class ClientProfile:
    profile_id: str
    base_url: str
    auth_mode: str
    environment: str
    auth_token: str | None = None
    timeout_seconds: float = 8.0


@dataclass(frozen=True)
class ClientResponse:
    ok: bool
    status_code: int | None
    state: DegradedState
    data: Any
    error: str | None = None
    retriable: bool = False

