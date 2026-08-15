"""The control facade path with the Native transition runtime attached.

These tests exercise the seam that switches the transition track on: a command
is admitted as a transition, driven to a terminal receipt, and answered from
persisted evidence.  They also pin the fail-closed behaviour that keeps an
unconfigured deployment on exactly its previous path.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_control_command_receipts import (
    COMMAND_RECEIPT_COMPLETED,
    COMMAND_RECEIPT_PENDING,
    WorkflowControlCommandReceipt,
)
from agent.services.workflow_control_composition import (
    AuthorizedWorkflowBackend,
    ConfiguredWorkflowBackendBridge,
)
from agent.services.workflow_transition_native_composition import (
    WorkflowCommandTransitionRuntime,
    WorkflowTransitionDriver,
    WorkflowTransitionDriveReport,
    WorkflowTransitionNativeCompositionError,
)
from agent.services.workflow_transition_outbox import TRANSITION_RUNTIME_NATIVE
from tests.test_workflow_transition_native_composition import (
    _binding,
    _receipt,
)


class _Admission:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def stage_or_adopt(self, *, receipt: Any, binding: Any) -> Any:
        self.calls.append((receipt.command_id, binding.workflow_id))
        if self.error is not None:
            raise self.error
        return None


class _Runner:
    """Drain stub that terminates the receipt after a configured tick count."""

    def __init__(self, receipts: "_Receipts", *, completes_after: int = 1) -> None:
        self._receipts = receipts
        self._completes_after = completes_after
        self.ticks = 0

    def drain(self, *, limit: int) -> tuple[Any, ...]:
        del limit
        self.ticks += 1
        if self.ticks >= self._completes_after:
            self._receipts.complete()
        return ()


class _Receipts:
    def __init__(self, receipt: WorkflowControlCommandReceipt) -> None:
        self.receipt = receipt

    def get(self, command_id: str) -> WorkflowControlCommandReceipt | None:
        return self.receipt if command_id == self.receipt.command_id else None

    def complete(self) -> None:
        self.receipt = WorkflowControlCommandReceipt(
            **{
                **{
                    field: getattr(self.receipt, field)
                    for field in (
                        "command_id",
                        "tenant_id",
                        "workflow_id",
                        "run_id",
                        "actor_id",
                        "command_type",
                        "request_payload",
                        "expected_revision",
                        "checkpoint_ref",
                        "request_fingerprint",
                        "transition_id",
                        "effect_fingerprint",
                    )
                },
                "outcome_fingerprint": _OUTCOME_FINGERPRINT,
                "state": COMMAND_RECEIPT_COMPLETED,
                "result_status": {
                    "status": "running",
                    "revision": 8,
                    "checkpoint_ref": "checkpoint-8",
                },
            }
        )


class _Bindings:
    def __init__(self, binding: Any) -> None:
        self.binding = binding

    def get(self, workflow_id: str) -> Any:
        return self.binding if workflow_id == self.binding.workflow_id else None


_EFFECT_FINGERPRINT = "a" * 64
_OUTCOME_FINGERPRINT = "b" * 64


def _attributed_receipt() -> WorkflowControlCommandReceipt:
    base = _receipt()
    return WorkflowControlCommandReceipt(
        command_id=base.command_id,
        tenant_id=base.tenant_id,
        workflow_id=base.workflow_id,
        run_id=base.run_id,
        actor_id=base.actor_id,
        command_type=base.command_type,
        request_payload=base.request_payload,
        expected_revision=base.expected_revision,
        checkpoint_ref=base.checkpoint_ref,
        request_fingerprint=base.request_fingerprint,
        transition_id="transition-a",
        effect_fingerprint=_EFFECT_FINGERPRINT,
        state=COMMAND_RECEIPT_PENDING,
    )


def _backend(
    *,
    receipts: _Receipts,
    transitions: WorkflowCommandTransitionRuntime | None,
) -> AuthorizedWorkflowBackend:
    backend = AuthorizedWorkflowBackend.__new__(AuthorizedWorkflowBackend)
    backend._command_receipts = receipts  # type: ignore[attr-defined]
    backend._bindings = _Bindings(_binding())  # type: ignore[attr-defined]
    backend._transitions = transitions  # type: ignore[attr-defined]
    return backend


def _runtime(runner: _Runner, *, admission: _Admission | None = None) -> WorkflowCommandTransitionRuntime:
    return WorkflowCommandTransitionRuntime(
        admission=admission or _Admission(),
        driver=WorkflowTransitionDriver(runner=runner, limit=8),
    )


def test_an_attributed_receipt_is_driven_to_its_persisted_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipts = _Receipts(_attributed_receipt())
    runner = _Runner(receipts, completes_after=1)
    backend = _backend(receipts=receipts, transitions=_runtime(runner))
    monkeypatch.setattr(
        "agent.services.workflow_control_composition.validate_persisted_public_status",
        lambda *args, **kwargs: None,
    )

    result = backend._recover_command_receipt(receipts.receipt)

    assert result == {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }
    assert runner.ticks == 1


def test_a_transition_that_never_terminates_stays_pending_and_is_never_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipts = _Receipts(_attributed_receipt())
    runner = _Runner(receipts, completes_after=99)
    backend = _backend(receipts=receipts, transitions=_runtime(runner))
    monkeypatch.setattr(
        "agent.services.workflow_control_composition.validate_persisted_public_status",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(RuntimeError, match="workflow_control_command_transition_pending"):
        backend._recover_command_receipt(receipts.receipt)

    # Bounded, not unbounded: the drive budget is spent and then it fails closed.
    assert 1 <= runner.ticks <= 8


def test_an_unconfigured_deployment_keeps_the_previous_fail_closed_behaviour() -> None:
    receipts = _Receipts(_attributed_receipt())
    backend = _backend(receipts=receipts, transitions=None)

    with pytest.raises(RuntimeError, match="workflow_control_command_transition_pending"):
        backend._recover_command_receipt(receipts.receipt)


def test_the_receipt_is_never_claimed_out_from_under_a_live_transition() -> None:
    """An attributed receipt must not take the synchronous claim path.

    Claiming would race the runner's own lease, so the drive path is the only
    legitimate route.  A claim store that raises proves it is never touched.
    """

    class _ExplodingReceipts(_Receipts):
        def claim(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("an attributed receipt must never be claimed here")

    receipts = _ExplodingReceipts(_attributed_receipt())
    backend = _backend(receipts=receipts, transitions=None)

    with pytest.raises(RuntimeError, match="transition_pending"):
        backend._recover_command_receipt(receipts.receipt)


def test_the_runtime_bundle_rejects_a_half_configured_transition_path() -> None:
    runner = _Runner(_Receipts(_attributed_receipt()))
    driver = WorkflowTransitionDriver(runner=runner, limit=4)

    with pytest.raises(WorkflowTransitionNativeCompositionError, match="admission_invalid"):
        WorkflowCommandTransitionRuntime(admission=object(), driver=driver)  # type: ignore[arg-type]
    with pytest.raises(WorkflowTransitionNativeCompositionError, match="driver_invalid"):
        WorkflowCommandTransitionRuntime(admission=_Admission(), driver=object())  # type: ignore[arg-type]


def test_drive_report_is_countable_for_the_reconcile_summary() -> None:
    report = WorkflowTransitionDriveReport(2, ("progressed", "completed"))

    assert report.to_dict() == {
        "runtime_id": TRANSITION_RUNTIME_NATIVE,
        "processed": 2,
        "outcomes": ["progressed", "completed"],
    }


class _TraceState:
    def __init__(self) -> None:
        self.marked: list[tuple[str, int]] = []
        self.error: Exception | None = None

    def mark_pending(self, workflow_id: str, *, revision: int) -> None:
        if self.error is not None:
            raise self.error
        self.marked.append((workflow_id, revision))

    def list_pending(self, *, limit: int) -> tuple[Any, ...]:
        del limit
        return ()

    def acknowledge(self, workflow_id: str, *, revision: int, cursor: str) -> bool:
        del workflow_id, revision, cursor
        return True


def _bridge(trace_state: Any) -> Any:
    bridge = ConfiguredWorkflowBackendBridge.__new__(ConfiguredWorkflowBackendBridge)
    bridge._trace_state = trace_state  # type: ignore[attr-defined]
    bridge._read_models = None  # type: ignore[attr-defined]
    return bridge


def test_a_terminal_status_marks_the_trace_pending_at_its_own_revision() -> None:
    state = _TraceState()
    bridge = _bridge(state)

    bridge._project(_binding(), {"status": "completed", "revision": 9})

    assert state.marked == [("workflow-a", 9)]


def test_a_running_status_never_marks_a_trace_pending() -> None:
    state = _TraceState()
    bridge = _bridge(state)

    bridge._project(_binding(), {"status": "running", "revision": 9})

    assert state.marked == []


def test_an_unavailable_trace_store_never_fails_the_run_it_was_bookkeeping_for() -> None:
    """Marking is bookkeeping; a run must not break because it was unavailable."""

    state = _TraceState()
    state.error = TimeoutError("trace state unavailable")
    bridge = _bridge(state)

    bridge._project(_binding(), {"status": "completed", "revision": 9})

    assert state.marked == []


def test_a_deployment_without_trace_state_keeps_its_previous_projection_path() -> None:
    bridge = _bridge(None)

    bridge._project(_binding(), {"status": "completed", "revision": 9})
