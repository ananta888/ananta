"""Bounded browser operation polling; no human or model dependency."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from worker.meet_media.speech_browser import _CANCEL, _START, _STATE, BrowserSpeechPort


def test_unresolved_open_does_not_suspend_authority_or_deadline_checks():
    now = 0
    page = Mock(url="https://meet.example/machine")
    page.evaluate.return_value = {"state": "pending", "result": None}

    def advance(milliseconds):
        nonlocal now
        now += milliseconds / 1000

    page.wait_for_timeout.side_effect = advance
    current = Mock()
    port = BrowserSpeechPort(page, current, clock=lambda: now)
    with pytest.raises(ValueError, match="setup_timeout"):
        port.open("speech:session", 441)
    assert 10 <= now < 10.1
    assert current.call_count > 200
    assert page.evaluate.call_args.args[0] == _CANCEL


def test_revocation_after_poll_and_navigation_discard_late_open():
    for navigate in (False, True):
        page = Mock(url="https://meet.example/machine")
        page.evaluate.return_value = {"state": "pending", "result": None}
        current = Mock()
        port = BrowserSpeechPort(page, current)

        def invalidate(_milliseconds):
            if navigate:
                page.url = "https://other.example"
            else:
                current.side_effect = PermissionError("test_revoked")

        page.wait_for_timeout.side_effect = invalidate
        with pytest.raises((PermissionError, ValueError)):
            port.open("speech:session", 441)
        assert (page.evaluate.call_args.args[0] == _CANCEL) is not navigate


def test_new_url_cannot_receive_status_pcm_or_cleanup_calls():
    page = Mock(url="https://meet.example/machine")
    port = BrowserSpeechPort(page, lambda: None)
    page.url = "https://other.example"
    with pytest.raises(ValueError, match="navigation_denied"):
        port.status()
    with pytest.raises(ValueError, match="navigation_denied"):
        port.push(1, 0, "AQI=")
    port.close(1)
    page.evaluate.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"state": "failed", "result": None},
        {"state": "stale", "result": None},
        {"state": "done", "result": {}, "extra": True},
    ],
)
def test_closed_operation_state_cancels_unknown_or_failed_responses(value):
    page = Mock(url="https://meet.example/machine")
    page.evaluate.return_value = value
    with pytest.raises(ValueError, match="setup_failed"):
        BrowserSpeechPort(page, lambda: None).open("speech:session", 441)
    assert page.evaluate.call_args.args[0] == _CANCEL


def test_actual_js_phase_fences_late_settlement_and_cleans_only_its_generation():
    # Execute the exact browser boundary functions with deterministic Promises.
    # This is JavaScript lifecycle coverage, explicitly not a WebRTC browser gate.
    script = """const assert = require('node:assert/strict');
      const [start, state, cancel] = JSON.parse(process.argv[1]).map(code => eval('(' + code + ')'));
      let generation = 0, closed = [], settle;
      global.window = {anantaMachine:{speech:{
        open() { generation++; return new Promise(resolve => {settle = resolve}); },
        status() { return {generation}; }, close() {closed.push(generation); generation++;}
      }}};
      (async () => {
        start(['old', 'speech:session', 441]); const late = settle;
        assert.equal(state('old').state, 'pending');
        cancel('old'); assert.deepEqual(closed, [1]);
        start(['fresh', 'speech:session', 441]); const fresh = settle;
        late({generation:1}); await Promise.resolve();
        assert.equal(state('fresh').state, 'pending'); assert.deepEqual(closed, [1]);
        cancel('old'); assert.deepEqual(closed, [1]);
        fresh({generation:3}); await Promise.resolve();
        assert.equal(state('fresh').state, 'done');
        // External replacement before cleanup cannot be stopped by the old phase.
        generation=7; cancel('fresh'); assert.deepEqual(closed, [1]);
        start(['oversize', 'speech:session', 441]); settle({text:'x'.repeat(1025)});
        await Promise.resolve(); assert.equal(state('oversize').state, 'cancelled');
        assert.deepEqual(closed, [1,8]);
      })().catch(() => {process.exitCode=1;});"""
    result = subprocess.run(
        ["node", "-e", script, json.dumps([_START, _STATE, _CANCEL])],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, "bounded JavaScript speech phase failed"
