from __future__ import annotations

from dataclasses import dataclass

from worker.core.tool_calling_pipeline import (
    ToolCallDecision,
    ToolCallingPipeline,
    ToolCallRequest,
)
from worker.core.tool_registry import ToolResult, build_default_registry


@dataclass
class Gate:
    allowed: bool = True
    reason: str = "allow"

    def verify(self, request, descriptor):
        return ToolCallDecision(self.allowed, self.reason)

    def authorize(self, request, descriptor):
        return ToolCallDecision(self.allowed, self.reason)

    def reserve(self, request, descriptor):
        return ToolCallDecision(self.allowed, self.reason)


class Ledger:
    def __init__(self) -> None:
        self.claims = []
        self.completed = []
        self.failed = []

    def claim(self, **kwargs):
        self.claims.append(kwargs)
        return ToolCallDecision(True, "claimed")

    def complete(self, **kwargs):
        self.completed.append(kwargs)

    def fail(self, **kwargs):
        self.failed.append(kwargs)


class Invoker:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def invoke(self, request, descriptor, *, limits):
        self.calls.append((request, descriptor, limits))
        if self.fail:
            raise RuntimeError("boom")
        return ToolResult(
            tool_id=request.tool_id,
            execution_id=request.attempt_id,
            success=True,
        )


class Audit:
    def __init__(self) -> None:
        self.events = []

    def record(self, event):
        self.events.append(dict(event))


def request(tool_id: str = "apply_patch") -> ToolCallRequest:
    arguments = {"patch_artifact_id": "patch-1"} if tool_id == "apply_patch" else {}
    return ToolCallRequest(
        tenant_id="tenant-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        fencing_token=7,
        tool_id=tool_id,
        arguments=arguments,
        authorization_envelope={"token": "opaque"},
    )


def pipeline(*, gate: Gate | None = None, invoker: Invoker | None = None):
    selected_gate = gate or Gate()
    ledger = Ledger()
    selected_invoker = invoker or Invoker()
    audit = Audit()
    return (
        ToolCallingPipeline(
            registry=build_default_registry(),
            authorization=selected_gate,
            policy=selected_gate,
            budget=selected_gate,
            approval=selected_gate,
            ledger=ledger,
            invoker=selected_invoker,
            audit=audit,
        ),
        ledger,
        selected_invoker,
        audit,
    )


def test_unregistered_tool_is_never_reported_as_invoked() -> None:
    subject, ledger, invoker, audit = pipeline()

    outcome = subject.execute(request("missing-tool"))

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_not_registered"
    assert invoker.calls == []
    assert ledger.claims == []
    assert audit.events[-1]["event_type"] == "workflow.tool.blocked"


def test_policy_denial_stops_before_ledger_and_invoker() -> None:
    subject, ledger, invoker, _audit = pipeline(gate=Gate(False, "policy_denied"))

    outcome = subject.execute(request())

    assert outcome.status == "blocked"
    assert outcome.reason_code == "policy_denied"
    assert ledger.claims == []
    assert invoker.calls == []


def test_side_effect_is_claimed_with_fencing_and_completed() -> None:
    subject, ledger, invoker, _audit = pipeline()

    outcome = subject.execute(request())

    assert outcome.status == "success"
    assert len(invoker.calls) == 1
    assert ledger.claims[0]["fencing_token"] == 7
    assert ledger.completed[0]["operation_id"] == outcome.operation_id


def test_operation_id_is_stable_across_attempts_and_argument_replays() -> None:
    first = request()
    second = ToolCallRequest(
        **{
            **first.__dict__,
            "attempt_id": "attempt-2",
            "arguments": {"patch_artifact_id": "patch-2"},
        }
    )

    assert first.resolved_operation_id() == second.resolved_operation_id()


def test_forged_operation_id_is_blocked_before_any_gate_or_invocation() -> None:
    subject, ledger, invoker, audit = pipeline()
    forged = ToolCallRequest(**{**request().__dict__, "operation_id": "op-forged"})

    outcome = subject.execute(forged)

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_operation_id_mismatch"
    assert ledger.claims == []
    assert invoker.calls == []
    assert audit.events[-1]["event_type"] == "workflow.tool.blocked"


def test_exception_marks_side_effect_uncertain() -> None:
    subject, ledger, _invoker, _audit = pipeline(invoker=Invoker(fail=True))

    outcome = subject.execute(request())

    assert outcome.status == "failed"
    assert ledger.failed[0]["uncertain"] is True


def test_extra_arguments_are_blocked_before_authorization_or_invocation() -> None:
    subject, ledger, invoker, _audit = pipeline()
    invalid = ToolCallRequest(
        **{
            **request().__dict__,
            "arguments": {
                "patch_artifact_id": "patch-1",
                "escalated_scope": "admin",
            },
        }
    )

    outcome = subject.execute(invalid)

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_arguments_invalid"
    assert ledger.claims == []
    assert invoker.calls == []


def test_arguments_and_results_are_redacted_at_the_invocation_boundary() -> None:
    selected = Invoker()

    def invoke(request, descriptor, *, limits):
        selected.calls.append((request, descriptor, limits))
        return ToolResult(
            tool_id=request.tool_id,
            execution_id=request.attempt_id,
            success=True,
            stdout="secret-value result",
        )

    selected.invoke = invoke
    subject, _ledger, _invoker, audit = pipeline(invoker=selected)
    bound = ToolCallRequest(
        **{
            **request().__dict__,
            "arguments": {"patch_artifact_id": "secret-value"},
            "secret_refs": ("secret-value",),
        }
    )

    outcome = subject.execute(bound)

    assert selected.calls[0][0].arguments["patch_artifact_id"] == "[REDACTED]"
    assert outcome.result is not None
    assert outcome.result.stdout == "[REDACTED] result"
    assert any(event["event_type"] == "workflow.tool.redaction_checked" for event in audit.events)


def test_langchain_registry_exposes_no_direct_execution_bypass() -> None:
    from worker.adapters.lc_tool_registry import get_tools_for_chain

    assert get_tools_for_chain(["search_code", "summarize_doc"]) == []
