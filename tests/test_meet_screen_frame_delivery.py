"""Exact screen phase transport and generation-owned cancellation; no capture."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.screen_frame_delivery import CANCEL, CLOSE, POLL, START, BrowserScreenFrames

URL = "https://synthetic.test/machine"


def setup():
    clock = SimpleNamespace(now=100.0)
    page = Mock(url=URL)
    frames = BrowserScreenFrames(page, url=URL, clock=lambda: clock.now)
    return clock, page, frames


def test_begin_and_poll_are_single_rpc_steps_without_waiting_or_queueing():
    clock, page, frames = setup()
    frames.begin(2, 1, "synthetic-jpeg")
    token = frames.token
    assert len(token) == 32 and frames.busy and page.evaluate.call_count == 1
    assert page.evaluate.call_args.args == (START, [token, 2, 1, "synthetic-jpeg", URL])
    with pytest.raises(ValueError, match="busy"):
        frames.begin(2, 2, "another-jpeg")
    assert page.evaluate.call_count == 1
    page.evaluate.return_value = "pending"
    for offset in [0.1, 0.3, 0.9]:
        clock.now = 100 + offset
        assert frames.poll() == "pending" and frames.token == token
    page.evaluate.return_value = "done"
    assert frames.poll() == "done" and not frames.busy
    assert page.evaluate.call_args.args == (POLL, [token, URL])
    with pytest.raises(ValueError, match="not_pending"):
        frames.poll()
    assert page.evaluate.call_count == 5
    page.wait_for_timeout.assert_not_called()
    frames.begin(2, 2, "latest-frame")
    assert frames.busy and frames.token != token


@pytest.mark.parametrize("state", [None, {}, True, "failed", "unknown"])
def test_invalid_or_failed_phase_cancels_without_retrying(state):
    _, page, frames = setup()
    frames.begin(2, 1, "jpeg")
    token = frames.token
    page.evaluate.return_value = state
    with pytest.raises(ValueError, match="frame_rejected"):
        frames.poll()
    assert not frames.busy
    assert page.evaluate.call_args.args == (CANCEL, [token, URL])
    frames.cancel()
    assert page.evaluate.call_count == 3


def test_exact_delivery_deadline_cancels_even_when_a_late_done_is_waiting():
    clock, page, frames = setup()
    frames.begin(2, 1, "jpeg")
    token = frames.token
    clock.now += 1.5
    page.evaluate.return_value = "done"
    with pytest.raises(ValueError, match="delivery_timeout"):
        frames.poll()
    assert not frames.busy and page.evaluate.call_count == 2
    assert page.evaluate.call_args.args == (CANCEL, [token, URL])


def test_expiry_after_status_read_cannot_be_accepted_as_timely_completion():
    clock, page, frames = setup()
    frames.begin(2, 1, "jpeg")

    def evaluate(script, args):
        if script == POLL:
            clock.now += 1.5
        return "done"

    page.evaluate.side_effect = evaluate
    with pytest.raises(ValueError, match="delivery_timeout"):
        frames.poll()
    assert not frames.busy


def test_navigation_forbids_further_frame_or_cleanup_rpc_on_foreign_page():
    _, page, frames = setup()
    frames.begin(2, 1, "jpeg")
    page.url += "/foreign"
    with pytest.raises(ValueError, match="navigation_denied"):
        frames.poll()
    frames.close_generation(2)
    assert not frames.busy and page.evaluate.call_count == 1


@pytest.mark.parametrize(
    "generation,sequence,jpeg",
    [(True, 1, "x"), (0, 1, "x"), (2**53, 1, "x"), (1, False, "x"), (1, 0, "x"), (1, 1, ""), (1, 1, "x" * 350001)],
)
def test_invalid_frame_bounds_reject_before_browser_io(generation, sequence, jpeg):
    _, page, frames = setup()
    with pytest.raises(ValueError, match="frame_invalid"):
        frames.begin(generation, sequence, jpeg)
    assert not frames.busy
    page.evaluate.assert_not_called()


def test_known_closed_activation_is_not_promoted_to_success_or_retried():
    _, page, frames = setup()
    frames.begin(2, 1, "jpeg")
    page.evaluate.return_value = "stale"
    assert frames.poll() == "stale" and not frames.busy
    assert page.evaluate.call_count == 2


@pytest.mark.parametrize(
    "current,expected",
    [
        ({"open": False, "generation": 2, "sequence": 0}, "stale"),
        ({"open": True, "generation": 4, "sequence": 0}, "failed"),
        ({"open": True, "generation": 2, "sequence": 1}, "failed"),
    ],
)
def test_actual_start_checks_authority_without_a_separate_preflight(current, expected):
    script = """
      const assert = require('node:assert/strict');
      const [startText,pollText,current,expected] = JSON.parse(process.argv[1]);
      const start=eval('('+startText+')'),poll=eval('('+pollText+')');
      let pushes=0;
      global.window={location:{href:'synthetic'},anantaMachine:{screen:{
        status:()=>current,push:()=>{pushes++;throw new Error('must not push')}
      }}};
      start(['phase',2,1,'jpeg','synthetic']);
      assert.equal(poll(['phase','synthetic']),expected);
      assert.equal(pushes,0);assert.equal(window.__anantaScreenFrame,undefined);
    """
    result = subprocess.run(
        ["node", "-e", script, json.dumps([START, POLL, current, expected])], capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr.decode()


def test_actual_javascript_keeps_one_slot_until_browser_submission_spacing_is_safe():
    script = """
      const assert = require('node:assert/strict');
      const [start,poll,cancel] = JSON.parse(process.argv[1]).map(s=>eval('('+s+')'));
      let now=0, sequence=0, generation=2, open=true, resolve, pushes=0, closes=0;
      global.performance={now:()=>now};
      global.window={location:{href:'synthetic'},anantaMachine:{screen:{
        status:()=>({open,generation,sequence}),
        push(){pushes++;return new Promise(yes=>{resolve=yes})},
        close(){closes++;open=false}
      }}};
      const flush=()=>new Promise(r=>setImmediate(r));
      (async()=>{
        start(['early',2,1,'jpeg','synthetic']);
        now=80;sequence=1;resolve();await flush();
        assert.equal(poll(['early','synthetic']),'pending');
        assert.throws(()=>start(['backlog',2,2,'jpeg','synthetic']),/frame_busy/);
        now=279.999;assert.equal(poll(['early','synthetic']),'pending');
        now=280;assert.equal(poll(['early','synthetic']),'done');
        assert.equal(window.__anantaScreenFrame,undefined);
        start(['late',2,2,'jpeg','synthetic']);
        now=300;sequence=2;resolve();await flush();
        now=700;assert.equal(poll(['late','synthetic']),'done');
        assert.equal(pushes,2);
        start(['cancel',2,3,'jpeg','synthetic']);
        now=720;sequence=3;resolve();await flush();
        cancel(['cancel','synthetic']);now=1000;
        assert.equal(poll(['cancel','synthetic']),'failed');
        assert.equal(closes,1);assert.equal(pushes,3);
        open=true;generation=4;sequence=0;
        start(['backward',4,1,'jpeg','synthetic']);
        sequence=1;resolve();await flush();now=999;
        assert.equal(poll(['backward','synthetic']),'failed');
      })().catch(e=>{console.error(e);process.exitCode=1});
    """
    result = subprocess.run(["node", "-e", script, json.dumps([START, POLL, CANCEL])], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_actual_javascript_prevents_late_frame_settlement_from_touching_replacement_generation():
    script = """
      const assert = require('node:assert/strict');
      const [start,poll,cancel,close] = JSON.parse(process.argv[1]).map(s => eval('('+s+')'));
      let generation=2, sequence=0, open=true, pushes=0, closes=[], resolve, reject;
      let now=0; global.performance={now:()=>now};
      global.window = {location:{href:'synthetic'}, anantaMachine:{screen:{
        status:()=>({open,generation,sequence}),
        push(){pushes++;return new Promise((yes,no)=>{resolve=yes;reject=no})},
        close(){closes.push(generation);open=false;generation++;}
      }}};
      const flush = () => new Promise(r=>setImmediate(r));
      (async()=>{
        assert.equal(start(['old',2,1,'private-jpeg','synthetic']),undefined);
        const late=resolve;
        assert.equal(poll(['old','synthetic']),'pending');
        assert.throws(()=>start(['backlog',2,2,'jpeg','synthetic']),/frame_busy/);
        cancel(['old','synthetic']); assert.deepEqual(closes,[2]);
        open=true;generation=4;
        start(['fresh',4,1,'new-jpeg','synthetic']);const current=resolve;
        late(); await flush(); assert.equal(poll(['fresh','synthetic']),'pending');
        cancel(['old','synthetic']);close([2,'synthetic']);assert.deepEqual(closes,[2]);
        sequence=1;current();await flush();now=200;assert.equal(poll(['fresh','synthetic']),'done');
        assert.equal(window.__anantaScreenFrame,undefined);
        start(['failing',4,2,'jpeg','synthetic']);reject(new Error('PRIVATE_ERROR'));await flush();
        assert.deepEqual(window.__anantaScreenFrame,{token:'failing',generation:4,sequence:2,state:'failed'});
        assert.equal(poll(['failing','synthetic']),'failed');
        start(['expiry',4,2,'jpeg','synthetic']);open=false;
        reject(new Error('meet_screen_authority_changed'));await flush();
        assert.equal(poll(['expiry','synthetic']),'stale');
        open=true;generation=6;sequence=0;
        start(['foreign',4,1,'jpeg','synthetic']);assert.equal(poll(['foreign','synthetic']),'failed');
        assert.equal(pushes,4);
        window.location.href='foreign';
        assert.throws(()=>start(['nav',6,1,'jpeg','synthetic']),/navigation_denied/);
        close([6,'synthetic']);assert.deepEqual(closes,[2]);
      })().catch(e=>{console.error(e);process.exitCode=1});
    """
    result = subprocess.run(
        ["node", "-e", script, json.dumps([START, POLL, CANCEL, CLOSE])], capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr.decode()
