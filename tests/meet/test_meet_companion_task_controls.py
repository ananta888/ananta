"""Bounded Hub source control facts, deterministic futures, actual child abort."""

import sys
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionTaskControls


def fixture():
    now = [100.0]
    client = SimpleNamespace(deadline=700.0, read=Mock())
    pool = Mock()
    pool.submit.side_effect = lambda *_args: Future()
    gate = CompanionTaskControls(client, clock=lambda: now[0], pool=pool)

    def control(enabled):
        return {"enabled": enabled, "revision": 1, "since": 1000}

    state = {"controls": {"revision": 1, "chat": control(True), "speech": control(True), "avatar": control(True),
                           "audio": control(False), "screen": control(False)}}
    gate.step()
    gate.exchange.pending.set_result(state)
    gate.step()
    return now, gate, state, pool


def test_hub_pause_fences_only_the_changed_source_and_never_enables_it_locally():
    now, gate, state, _pool = fixture()
    chat, speech = gate.checkpoint("chat"), gate.checkpoint("speech")
    now[0] += 1
    gate.step()
    state["controls"]["revision"] = 2
    state["controls"]["speech"] = {"enabled": False, "revision": 2, "since": 2000}
    gate.exchange.pending.set_result(state)
    gate.step()
    gate.require_current(chat)
    with pytest.raises(CompanionControlLost):
        gate.require_current(speech)
    with pytest.raises(CompanionControlLost):
        gate.checkpoint("speech")
    gate.close()


def test_slow_response_cannot_extend_old_request_freshness():
    now, gate, _state, _pool = fixture()
    checkpoint = gate.checkpoint("chat")
    now[0] += 2.5
    with pytest.raises(CompanionControlLost):
        gate.require_current(checkpoint)
    with pytest.raises(CompanionControlLost):
        gate.step()
    assert gate.closed


def test_response_latency_consumes_freshness_instead_of_extending_it():
    now, gate, state, _pool = fixture()
    now[0] = 101
    gate.step()
    now[0] = 102.4
    gate.exchange.pending.set_result(state)
    gate.step()
    checkpoint = gate.checkpoint("chat")
    assert gate.fresh_until == 103.5
    now[0] = 103.5
    with pytest.raises(CompanionControlLost):
        gate.require_current(checkpoint)
    gate.close()


def test_failed_hub_read_clears_authority_and_never_reuses_cached_state():
    now, gate, _state, _pool = fixture()
    checkpoint = gate.checkpoint("chat")
    now[0] += 1
    gate.step()
    gate.exchange.pending.set_exception(ValueError("synthetic_task_cancelled"))
    with pytest.raises(CompanionControlLost, match="meet_companion_task_control_lost"):
        gate.step()
    assert gate.state is None
    with pytest.raises(CompanionControlLost):
        gate.require_current(checkpoint)


def test_hub_resume_does_not_restore_operations_started_before_pause():
    now, gate, state, _pool = fixture()
    old = gate.checkpoint("speech")
    for revision, enabled in [(2, False), (3, True)]:
        now[0] += 1
        gate.step()
        state["controls"]["revision"] = revision
        state["controls"]["speech"] = {"enabled": enabled, "revision": revision, "since": revision * 1000}
        gate.exchange.pending.set_result(state)
        gate.step()
    with pytest.raises(CompanionControlLost):
        gate.require_current(old)
    gate.require_current(gate.checkpoint("speech"))
    gate.close()


@pytest.mark.parametrize("change", ["revision", "unfenced", "since"])
def test_regressed_or_unfenced_hub_control_is_terminal(change):
    now, gate, state, _pool = fixture()
    now[0] += 1
    gate.step()
    if change == "revision":
        state["controls"]["revision"] = 0
    elif change == "since":
        state["controls"]["chat"]["since"] = 999
    else:
        state["controls"]["chat"]["enabled"] = False
    gate.exchange.pending.set_result(state)
    with pytest.raises(CompanionControlLost):
        gate.step()
    assert gate.closed


def test_checkpoint_cannot_cross_local_execution_owners():
    _now, one, _state, _pool = fixture()
    _now, two, _state, _pool = fixture()
    with pytest.raises(CompanionControlLost):
        two.require_current(one.checkpoint("chat"))
    one.close()
    two.close()


@pytest.mark.timeout(10)
def test_current_hub_chat_pause_aborts_actual_owned_model_without_fallback_reply():
    now, gate, state, _pool = fixture()
    checkpoint = gate.checkpoint("chat")
    execution = CompanionExecutionProcess(command=(sys.executable, '-c',
        "import sys,time;sys.stdin.readline();time.sleep(30)"))
    calls = [0]

    def cooperate():
        calls[0] += 1
        if calls[0] == 2:
            now[0] += 1
            gate.step()
            state["controls"]["revision"] = 2
            state["controls"]["chat"] = {"enabled": False, "revision": 2, "since": 2000}
            gate.exchange.pending.set_result(state)
            gate.step()
        gate.require_current(checkpoint)

    try:
        with pytest.raises(CompanionControlLost):
            execution.call("answer", "synthetic pending question", cooperate=cooperate)
        assert execution._process.poll() is not None
    finally:
        execution.close()
        gate.close()
