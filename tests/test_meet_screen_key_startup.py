"""Bounded synthetic fault injection, plus explicitly opted-in real transport."""

import json
import os
import subprocess

import pytest

from tests.meet_screen_key_startup import SCRIPT, ScreenKeyStartup


@pytest.mark.timeout(15)
def test_actual_javascript_delays_only_first_sender_key_and_wipes_only_its_held_key():
    script = r"""
      const assert = require('node:assert/strict');
      const timers = new Map(), sent = [];
      let now=100, timer=0, stopped=0;
      global.performance={now:()=>now};
      global.setTimeout=(fn, delay)=>{assert.equal(delay,2000);timers.set(++timer,fn);return timer};
      global.clearTimeout=id=>timers.delete(id);
      global.window={Worker:class {
        postMessage(message,...args){sent.push([message,args])}
        terminate(){stopped++}
      }};
      eval(JSON.parse(process.argv[1]));
      const key=()=>({version:1,type:'set-key',direction:'encrypt',contextId:'synthetic',
        keyId:'0000000000000001',baseKey:new Uint8Array(16).fill(42).buffer});
      const unrelated=new window.Worker('synthetic',{name:'other'}), first=key();
      unrelated.postMessage(first,[first.baseKey]);assert.equal(sent.length,1);
      const worker=new window.Worker('synthetic',{name:'sframe-media'}), delayed=key();
      worker.postMessage({...key(),direction:'decrypt'});assert.equal(sent.length,2);
      worker.postMessage(delayed,[delayed.baseKey]);assert.equal(sent.length,2);
      const duplicate=key();worker.postMessage(duplicate);worker.postMessage(delayed);
      assert.equal(sent.length,2);assert.deepEqual([...new Uint8Array(duplicate.baseKey)],Array(16).fill(0));
      assert.equal(timers.size,1);assert.equal(window.__testScreenKeyStartup.scheduled,1);
      now+=2000;timers.get(1)();assert.equal(sent.length,3);
      assert.equal(sent[2][0],delayed);assert.equal(sent[2][1][0][0],delayed.baseKey);
      assert.deepEqual([...new Uint8Array(delayed.baseKey)],Array(16).fill(42));
      assert.equal(window.__testScreenKeyStartup.delay_ms,2000);
      worker.postMessage(key());assert.equal(sent.length,4);
      const closed=new window.Worker('synthetic',{name:'sframe-media'}), held=key();
      closed.postMessage(held);const late=timers.get(2);closed.terminate();late();
      assert.equal(stopped,1);assert.equal(sent.length,4);
      assert.deepEqual([...new Uint8Array(held.baseKey)],Array(16).fill(0));
      assert.equal(window.__testScreenKeyStartup.cancelled,1);
      assert.deepEqual(Object.keys(window.__testScreenKeyStartup),['scheduled','delivered','cancelled','delay_ms']);
      for (const command of [{type:'clear-all'}, {type:'clear-context',contextId:'synthetic'},
        {...key(),keyId:'0000000000000002'}]) {
        const target=new window.Worker('synthetic',{name:'sframe-media'}), old=key();
        target.postMessage(old);const callback=timers.get(timer), before=sent.length;
        target.postMessage(command);callback();
        assert.equal(sent.length,before+(command.type==='set-key'?0:1));
        if(command.type!=='set-key')assert.equal(sent.at(-1)[0],command);
        assert.deepEqual([...new Uint8Array(old.baseKey)],Array(16).fill(0));
        target.terminate();
      }
      const bounded=new window.Worker('synthetic',{name:'sframe-media'}),callbacks=[];
      for(let n=1;n<=4;n++){
        bounded.postMessage({...key(),keyId:String(n).padStart(16,'0')});
        if(n<=3)callbacks.push(timers.get(timer));
      }
      const count=sent.length;callbacks.forEach(fn=>fn());assert.equal(sent.length,count);
    """
    result = subprocess.run(["node", "-e", script, json.dumps(SCRIPT)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.timeout(240)
@pytest.mark.parametrize("direction", ["sender", "receiver"])
@pytest.mark.skipif(
    os.environ.get("MEET_SCREEN_KEY_STARTUP_GATE") != "1", reason="opt-in private delayed-key transport"
)
def test_delayed_first_key_still_delivers_decoded_screen_without_security_fallback(
    app, tmp_path, monkeypatch, record_property, direction
):
    from tests.test_meet_dialog_cross_repository import (
        SOAK_SECONDS,
    )
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "startup regression has a fixed short budget"
    # This test's explicit opt-in also enables the existing private stdio driver.
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    monkeypatch.setenv("MEET_TEST_RECEIVER_KEY_DELAY", "1" if direction == "receiver" else "0")
    observation = ScreenKeyStartup(monkeypatch) if direction == "sender" else None
    try:
        run_gate(app, tmp_path, monkeypatch, False, False, None, False, False, record_property)
        if observation is not None:
            observation.verify()
    finally:
        if observation is not None:
            record_property("synthetic_sender_key_startup", observation.report())
