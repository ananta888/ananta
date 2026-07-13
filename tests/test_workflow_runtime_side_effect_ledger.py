from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.workflow_runtime import (
    FencingTokenError,
    InMemorySideEffectLedger,
    InvalidTransitionError,
    SQLiteSideEffectLedger,
    operation_id_for,
    side_effect_event,
)


@pytest.fixture(params=["memory", "sqlite"])
def ledger(request: pytest.FixtureRequest, tmp_path) -> Iterator[InMemorySideEffectLedger | SQLiteSideEffectLedger]:
    if request.param == "memory":
        yield InMemorySideEffectLedger()
        return
    value = SQLiteSideEffectLedger(tmp_path / "ledger.sqlite")
    yield value
    value.close()


def _plan(ledger):
    return ledger.plan(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="git.push:origin/main",
        side_effect_class="non_idempotent_write",
    )


def test_operation_id_is_stable_and_ledger_makes_exactly_once_claim_decision(ledger) -> None:
    planned = _plan(ledger)
    assert planned.operation_id == operation_id_for(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        declared_operation="git.push:origin/main",
    )
    assert _plan(ledger) == planned

    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=1,
        fencing_token=7,
        authorization_envelope_id="envelope-1",
    )
    claim = ledger.claim(
        planned.operation_id,
        expected_revision=authorized.revision,
        fencing_token=7,
        attempt_id="attempt-1",
    )
    duplicate = ledger.claim(
        planned.operation_id,
        expected_revision=claim.record.revision,
        fencing_token=7,
        attempt_id="attempt-1",
    )
    completed = ledger.complete(
        planned.operation_id,
        expected_revision=claim.record.revision,
        fencing_token=7,
        attempt_id="attempt-1",
        result_ref="artifact://push-result",
    )

    assert claim.acquired is True
    assert duplicate.acquired is False and duplicate.reason == "already_claimed"
    assert completed.status == "completed"
    event = side_effect_event(completed, correlation_id="corr", causation_id="command")
    assert event.event_type == "workflow.side_effect.completed"
    assert event.payload["operation_id"] == planned.operation_id
    assert ledger.claim(
        planned.operation_id,
        expected_revision=completed.revision,
        fencing_token=7,
        attempt_id="attempt-1",
    ).reason == "already_completed"
    assert ledger.get(tenant_id="tenant-b", operation_id=planned.operation_id) is None


def test_stale_owner_cannot_finish_and_uncertain_operation_is_not_retried(ledger) -> None:
    planned = _plan(ledger)
    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=1,
        fencing_token=3,
        authorization_envelope_id="envelope-1",
    )
    started = ledger.claim(
        planned.operation_id,
        expected_revision=authorized.revision,
        fencing_token=3,
        attempt_id="attempt-1",
    ).record

    with pytest.raises(FencingTokenError):
        ledger.complete(
            planned.operation_id,
            expected_revision=started.revision,
            fencing_token=2,
            attempt_id="attempt-1",
            result_ref="artifact://bad",
        )

    uncertain = ledger.mark_uncertain(
        planned.operation_id,
        expected_revision=started.revision,
        fencing_token=3,
        attempt_id="attempt-1",
    )
    assert uncertain.status == "uncertain"
    with pytest.raises(InvalidTransitionError):
        ledger.authorize(
            planned.operation_id,
            expected_revision=uncertain.revision,
            fencing_token=4,
            authorization_envelope_id="envelope-2",
        )


def test_parallel_duplicate_delivery_has_one_side_effect_execution_decision(ledger) -> None:
    planned = _plan(ledger)
    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=1,
        fencing_token=8,
        authorization_envelope_id="envelope-1",
    )

    def claim_once():
        return ledger.claim(
            planned.operation_id,
            expected_revision=authorized.revision,
            fencing_token=8,
            attempt_id="delivery-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim_once(), range(2)))

    assert sorted(claim.acquired for claim in claims) == [False, True]
    assert {claim.reason for claim in claims} == {"acquired", "already_claimed"}


def test_crash_before_call_can_be_explicitly_failed_then_reauthorized(ledger) -> None:
    planned = _plan(ledger)
    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=planned.revision,
        fencing_token=4,
        authorization_envelope_id="envelope-1",
    )
    started = ledger.claim(
        planned.operation_id,
        expected_revision=authorized.revision,
        fencing_token=4,
        attempt_id="attempt-before-call",
    ).record
    failed = ledger.fail(
        planned.operation_id,
        expected_revision=started.revision,
        fencing_token=4,
        attempt_id="attempt-before-call",
        failure_code="external_call_not_started",
    )
    reauthorized = ledger.authorize(
        planned.operation_id,
        expected_revision=failed.revision,
        fencing_token=5,
        authorization_envelope_id="envelope-2",
    )

    assert failed.status == "failed"
    assert reauthorized.status == "authorized"
    assert reauthorized.fencing_token == 5


def test_completed_operation_can_be_compensated_with_an_auditable_result(ledger) -> None:
    planned = _plan(ledger)
    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=planned.revision,
        fencing_token=9,
        authorization_envelope_id="envelope-1",
    )
    started = ledger.claim(
        planned.operation_id,
        expected_revision=authorized.revision,
        fencing_token=9,
        attempt_id="attempt-1",
    ).record
    completed = ledger.complete(
        planned.operation_id,
        expected_revision=started.revision,
        fencing_token=9,
        attempt_id="attempt-1",
        result_ref="artifact://external-result",
    )
    compensated = ledger.compensate(
        planned.operation_id,
        expected_revision=completed.revision,
        fencing_token=9,
        result_ref="artifact://compensation-result",
    )

    assert compensated.status == "compensated"
    assert compensated.result_ref == "artifact://compensation-result"
    assert side_effect_event(
        compensated,
        correlation_id="corr",
        causation_id="compensation-command",
    ).event_type == "workflow.side_effect.compensated"
