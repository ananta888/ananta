"""Hub-owned Native graph queue port shared without worker imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.workflow_runtime.native_graph_contracts import (
    HubTaskReceipt,
    NativeNodeCommand,
    NativeNodeResult,
)


@dataclass(frozen=True)
class HubTaskSubmission:
    command: NativeNodeCommand
    receipt: HubTaskReceipt


class HubTaskSubmissionReadPort(Protocol):
    """Optional exact submission recovery, independent of queue mutation."""

    def get_submission(self, *, command_id: str, tenant_id: str, run_id: str) -> HubTaskSubmission | None: ...


class HubTaskQueuePort(Protocol):
    """Only Hub implementations may enqueue or control delegated node tasks."""

    def submit(self, command: NativeNodeCommand) -> HubTaskReceipt: ...

    def poll(
        self,
        *,
        tenant_id: str,
        run_id: str,
        hub_task_ids: tuple[str, ...],
    ) -> tuple[NativeNodeResult, ...]: ...

    def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        hub_task_ids: tuple[str, ...],
        reason: str,
    ) -> None: ...
