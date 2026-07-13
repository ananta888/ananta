"""Segregated Native graph ports preserving the Hub/worker boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from agent.services.workflow_runtime.native_graph_ports import HubTaskQueuePort as HubTaskQueuePort
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult


class NativeNodeHandlerPort(Protocol):
    """Worker business-operation seam; it cannot receive a task-queue port."""

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult: ...


class HubAuthorizationRevalidationPort(Protocol):
    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool: ...


class NativeAuthorizationVerifierPort(Protocol):
    """Verify Hub authority without exposing a signing interface to Workers."""

    def authorize(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
        tool: str = "",
        artifact: str = "",
        requested_budget: dict[str, int | float] | None = None,
        consume_nonce: bool = False,
        writing: bool = False,
        hub_revalidator: Callable[[RuntimeAuthorizationEnvelope], bool] | None = None,
        now: float | None = None,
    ) -> None: ...


class SideEffectLedgerGatewayPort(Protocol):
    """Worker client for the Hub-owned ledger, never a second local ledger."""

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> Any: ...

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> Any: ...

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> Any: ...

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> Any: ...


class RuntimePolicyRevalidationPort(Protocol):
    def allow_node(self, command: NativeNodeCommand) -> tuple[bool, str]: ...
