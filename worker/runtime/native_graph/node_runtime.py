"""Execution of exactly one node previously delegated by the Hub."""

from __future__ import annotations

import re
import time
import uuid

from agent.services.workflow_runtime.security import AuthorizationVerifier
from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.ports import (
    HubAuthorizationRevalidationPort,
    NativeNodeHandlerPort,
    RuntimePolicyRevalidationPort,
    SideEffectLedgerGatewayPort,
)


class NativeDelegatedNodeRuntime:
    """Worker-side runtime with no orchestration or task-creation capability."""

    runtime_id = "ananta-native-node"
    runtime_version = "1.0.0"

    def __init__(
        self,
        *,
        handler: NativeNodeHandlerPort,
        authorization_verifier: AuthorizationVerifier,
        policy: RuntimePolicyRevalidationPort,
        capabilities: frozenset[str],
        ledger: SideEffectLedgerGatewayPort | None = None,
        hub_revalidator: HubAuthorizationRevalidationPort | None = None,
        clock=time.time,
    ) -> None:
        self._handler = handler
        self._authorization = authorization_verifier
        self._policy = policy
        self._capabilities = frozenset(capabilities)
        self._ledger = ledger
        self._hub_revalidator = hub_revalidator
        self._clock = clock

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        command.assert_valid()
        missing = set(command.node.required_capabilities) - set(self._capabilities)
        if missing:
            return self._failed(command, hub_task_id, f"native_capability_missing:{','.join(sorted(missing))}")
        allowed, reason = self._policy.allow_node(command)
        if not allowed:
            return self._failed(command, hub_task_id, reason or "native_policy_denied")
        writing = command.node.side_effect_class in {"idempotent_write", "non_idempotent_write"}
        try:
            self._authorization.authorize(
                command.authorization,
                tenant_id=command.tenant_id,
                workflow_id=command.workflow_id,
                run_id=command.run_id,
                step_id=command.node.node_id,
                plan_hash=command.plan_hash,
                policy_version=command.policy_version,
                requested_budget=_requested_budget(command),
                consume_nonce=True,
                writing=writing,
                hub_revalidator=(
                    self._hub_revalidator.revalidate if self._hub_revalidator is not None else None
                ),
                now=float(self._clock()),
            )
        except Exception as exc:
            return self._failed(command, hub_task_id, _reason(exc, "native_authorization_denied"))

        claim_revision = 0
        if command.operation_id:
            if self._ledger is None:
                return self._failed(command, hub_task_id, "native_side_effect_ledger_unavailable")
            try:
                claim = self._ledger.claim(
                    command.operation_id,
                    expected_revision=command.side_effect_revision,
                    fencing_token=command.fencing_token,
                    attempt_id=command.attempt_id,
                )
                if not bool(claim.acquired):
                    if claim.reason == "already_completed":
                        return self._failed(command, hub_task_id, "native_side_effect_already_completed")
                    if claim.reason != "already_claimed":
                        return self._failed(command, hub_task_id, f"native_side_effect_{claim.reason}")
                claim_revision = int(claim.record.revision)
            except Exception as exc:
                return self._failed(command, hub_task_id, _reason(exc, "native_side_effect_claim_failed"))

        try:
            result = self._handler.execute(command, hub_task_id=hub_task_id)
            result.assert_valid()
            _assert_handler_result_binding(result, command, hub_task_id)
        except Exception as exc:
            if claim_revision and self._ledger is not None:
                try:
                    self._ledger.mark_uncertain(
                        command.operation_id,
                        expected_revision=claim_revision,
                        fencing_token=command.fencing_token,
                        attempt_id=command.attempt_id,
                        failure_code="native_handler_outcome_unknown",
                    )
                except Exception:
                    pass
            return self._failed(
                command,
                hub_task_id,
                _reason(exc, "native_handler_failed"),
                side_effect_status="uncertain" if claim_revision else "",
            )

        if claim_revision and self._ledger is not None:
            try:
                if result.status == "completed":
                    record = self._ledger.complete(
                        command.operation_id,
                        expected_revision=claim_revision,
                        fencing_token=command.fencing_token,
                        attempt_id=command.attempt_id,
                        result_ref=next(iter(result.artifact_refs.values()), f"result://{result.result_id}"),
                    )
                else:
                    record = self._ledger.fail(
                        command.operation_id,
                        expected_revision=claim_revision,
                        fencing_token=command.fencing_token,
                        attempt_id=command.attempt_id,
                        failure_code=result.reason_code or "native_node_failed",
                    )
                result = NativeNodeResult(
                    **{
                        **result.__dict__,
                        "side_effect_status": str(record.status),
                    }
                )
            except Exception as exc:
                return self._failed(
                    command,
                    hub_task_id,
                    _reason(exc, "native_side_effect_finalize_failed"),
                    side_effect_status="uncertain",
                )
        return result

    @staticmethod
    def _failed(
        command: NativeNodeCommand,
        hub_task_id: str,
        reason_code: str,
        *,
        side_effect_status: str = "",
    ) -> NativeNodeResult:
        return NativeNodeResult(
            result_id=f"nres-{uuid.uuid4().hex}",
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=command.node.node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status="failed",
            reason_code=reason_code,
            side_effect_status=side_effect_status,
        )


def _requested_budget(command: NativeNodeCommand) -> dict[str, int | float]:
    budget = command.node.budget
    if budget is None:
        return {}
    values: dict[str, int | float] = {
        "attempts": budget.max_attempts,
        "timeout_seconds": budget.timeout_seconds,
    }
    if budget.max_tokens is not None:
        values["tokens"] = budget.max_tokens
    if budget.max_cost_micros is not None:
        values["cost_micros"] = budget.max_cost_micros
    return values


def _assert_handler_result_binding(
    result: NativeNodeResult, command: NativeNodeCommand, hub_task_id: str
) -> None:
    expected = {
        "command_id": command.command_id,
        "hub_task_id": hub_task_id,
        "tenant_id": command.tenant_id,
        "workflow_id": command.workflow_id,
        "run_id": command.run_id,
        "node_id": command.node.node_id,
        "attempt_id": command.attempt_id,
        "fencing_token": command.fencing_token,
    }
    if any(getattr(result, name) != value for name, value in expected.items()):
        raise ValueError("native_node_result_binding_mismatch")


def _reason(exc: Exception, fallback: str) -> str:
    text = str(exc).strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,159}", text):
        return text
    return fallback
