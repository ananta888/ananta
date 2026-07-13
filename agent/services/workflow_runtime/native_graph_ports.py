"""Hub-owned Native graph queue port shared without worker imports."""

from __future__ import annotations

from typing import Protocol

from agent.services.workflow_runtime.native_graph_contracts import (
    HubTaskReceipt,
    NativeNodeCommand,
    NativeNodeResult,
)


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
