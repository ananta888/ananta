"""Policy port applied inside the atomic Task persistence transaction."""

from __future__ import annotations

from typing import Any, Protocol


class TaskCompletionPolicyPort(Protocol):
    def apply(
        self,
        *,
        authoritative_task: Any | None,
        candidate_task: Any,
        session: Any,
    ) -> Any: ...


__all__ = ["TaskCompletionPolicyPort"]
