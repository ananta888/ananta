"""Current Hub authority read under the caller-owned Task transaction."""

from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


@dataclass(frozen=True, slots=True)
class PiResultAuthority:
    assignment_id: str
    assignment_revision: int
    worker_id: str
    worker_url: str
    ownership_revision: int
    grant_revision: int


class PiResultAuthorityPort(Protocol):
    def require_current(
        self, *, session: Any, command: NativeNodeCommand, task: Any, now: float,
    ) -> PiResultAuthority: ...
