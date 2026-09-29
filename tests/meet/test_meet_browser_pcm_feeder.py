"""Run the exact feeder scripts with bounded virtual clocks and a strict source."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder
from worker.meet_media.browser_pcm_feeder_scripts import CLOSE, PULSE, START, STATUS


@pytest.mark.parametrize(
    "scenario",
    [
        "delivery",
        "hub_stall",
        "mono_stall",
        "rollback",
        "navigation",
        "lease",
        "chat",
        "membership",
        "deadline",
        "source_expiry",
        "generation",
        "partial_batch",
        "bad_progress",
        "replacement",
        "stale_pulse",
        "invalid_pulse",
        "oversized_pcm",
        "busy",
    ],
)
def test_exact_browser_feeder_lifecycle(scenario):
    script = """
      const assert = require('node:assert/strict');
      const vm = require('node:vm');
      const [START, STATUS, PULSE, CLOSE] = JSON.parse(process.argv[1]);
      const scenario = process.argv[2];
      let now = 100000, mono = 100, timer, closed = 0, pushes = 0, maxBuffered = 0, storage;
      let native = {state:'open', generation:1, receivedSamples:0, playedSamples:0, bufferedSamples:0};
      const lease = {sessionId:'synthetic', generation:1, expiresAt:200000};
      let joined = true, chat = true;
      const location = {href:'https://meet.test/machine'};
      const receipt = {generation:1, totalSamples:88200, expiresAt:150000};
      const authority = {url:location.href, lease:{...lease}, deadline:200000, hubUntil:now+2500};
      const source = {
        status: () => ({...native}),
        push: (generation, start, encoded) => {
          assert.equal(generation, 1); assert.equal(start, native.receivedSamples);
          if (scenario === 'partial_batch' && pushes === 2) throw new Error('private source error');
          const bytes = Buffer.from(encoded, 'base64');
          assert(bytes.every(value => value === 17));
          native.receivedSamples += bytes.length/2; native.bufferedSamples += bytes.length/2; pushes++;
          maxBuffered = Math.max(maxBuffered, native.bufferedSamples); assert(maxBuffered <= 4410);
        },
        close: () => { closed++; native.state='closed'; native.generation++; native.bufferedSamples=0; },
      };
      class Bytes extends Uint8Array {
        static from(...args) { storage = Uint8Array.from(...args); return storage; }
      }
      const window = {anantaMachine:{speech:source, chat:{status:()=>({open:chat})},
        status:()=>({joined,lease:{...lease}})}};
      const context = vm.createContext({window, location, Uint8Array:Bytes, Date:{now:()=>now},
        performance:{now:()=>mono}, atob, btoa, setInterval:fn=>{timer=fn;return 1;}, clearInterval:()=>{timer=null;}});
      const run = (text, arg) => vm.runInContext('('+text+')', context)(arg);
      const pcm = Buffer.alloc(receipt.totalSamples*2,17).toString('base64');
      const start = () => run(START,['owned',receipt,structuredClone(authority),pcm]);
      const state = () => run(STATUS,'owned');
      if (scenario === 'oversized_pcm') {
        assert.throws(()=>run(START,['owned',receipt,authority,'A'.repeat(2352001)]));
        assert.equal(pushes,0); return;
      }
      start();
      if (scenario === 'busy') {
        assert.throws(start); assert.equal(closed,0); run(CLOSE,'owned'); return;
      }
      if (scenario === 'delivery') {
        // Four seconds of actual source consumption, with no Python progress poll or PCM RPC.
        for (let i=1;i<=201 && timer;i++) {
          now+=20; mono+=20;
          const consumed = Math.min(441,native.bufferedSamples);
          assert(consumed > 0 || native.playedSamples === receipt.totalSamples);
          native.playedSamples+=consumed; native.bufferedSamples-=consumed;
          if(native.playedSamples===receipt.totalSamples) {native.state='completed';native.generation=2;}
          if(i%50===0) run(PULSE,['owned',{...authority,hubUntil:now+2500}]);
          timer?.();
        }
        assert.equal(state().state,'completed'); assert.equal(state().source.playedSamples,88200);
        assert.equal(state().sent,88200); assert.equal(closed,0); assert.equal(timer,null);
        assert(storage.every(value=>value===0));
        assert.equal(run(PULSE,['owned',{...authority,hubUntil:now+2500}]),true);
        run(CLOSE,'owned'); assert.equal(closed,0); return;
      }
      if (scenario === 'replacement') {
        const old = timer; run(CLOSE,'owned');
        native.generation=7; native.state='open';
        window.__anantaPcmFeeder={token:'new',state:'open',cancel:()=>{throw new Error('foreign close');}};
        old(); run(CLOSE,'owned'); assert.equal(closed,1); assert.equal(native.generation,7); return;
      }
      if (scenario === 'hub_stall' || scenario === 'stale_pulse') {now+=2500;mono+=2500;}
      if (scenario === 'mono_stall') mono+=2500;
      if (scenario === 'rollback') now--;
      if (scenario === 'navigation') location.href+='?foreign';
      if (scenario === 'lease') lease.generation++;
      if (scenario === 'generation') native.generation++;
      if (scenario === 'deadline') {now=authority.deadline;mono+=100000;}
      if (scenario === 'source_expiry') {now=receipt.expiresAt;mono+=50000;}
      if (scenario === 'chat') chat=false;
      if (scenario === 'membership') joined=false;
      if (scenario === 'bad_progress') native.receivedSamples++;
      if (scenario === 'invalid_pulse') {
        assert.equal(run(PULSE,['owned',{...authority,hubUntil:now+2501}]),false);
      } else if (scenario === 'stale_pulse') {
        assert.equal(run(PULSE,['owned',{...authority,hubUntil:now+2500}]),false);
      } else timer?.();
      assert.equal(state().state,'failed'); assert.equal(closed,scenario === 'generation' ? 0 : 1);
      assert.equal(timer,null);
      assert(storage.every(value=>value===0));
      if (scenario === 'partial_batch') assert.equal(pushes,2);
      run(CLOSE,'wrong'); assert(window.__anantaPcmFeeder); run(CLOSE,'owned');
      assert.equal(closed,scenario === 'generation' ? 0 : 1);
    """
    result = subprocess.run(
        ["node", "-e", "(() => {" + script + "})()", json.dumps([START, STATUS, PULSE, CLOSE]), scenario],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_python_port_bounds_bytes_and_fences_navigation_and_cleanup():
    page = Mock(url="https://meet.test/machine")
    feeder = BrowserPcmFeeder(page, page.url)
    for pcm in (b"", b"x", "text", b"x" * 1764002):
        with pytest.raises(ValueError, match="input_invalid"):
            feeder.start(pcm, {}, {})
    page.evaluate.assert_not_called()
    feeder.start(b"ab", {}, {})
    token = feeder.token
    assert len(token) == 32
    with pytest.raises(ValueError, match="input_invalid"):
        feeder.start(b"ab", {}, {})
    page.url += "?changed"
    with pytest.raises(ValueError, match="navigation_denied"):
        feeder.status()
    calls = page.evaluate.call_count
    feeder.close()
    assert page.evaluate.call_count == calls and feeder.token is None
