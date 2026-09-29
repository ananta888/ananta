"""Exact JavaScript delay fixture keeps native module loading and unrelated calls."""

import subprocess

from tests.meet_speech_opening_delay import SCRIPT


def test_delay_affects_only_speech_worklet_completion_without_replacing_native_loading():
    code = """const assert = require('node:assert/strict');
      let timers=[], calls=[];
      global.AudioWorklet=class {addModule(...args){calls.push(args);return Promise.resolve('native-result')}};
      global.setTimeout=(callback,delay)=>{assert.equal(delay,3000);timers.push(callback)};
      new Function(process.argv[1])();
      (async()=>{
        const worklet=new AudioWorklet(); let completed=false;
        const pending=worklet.addModule('/assets/machine-speech.worklet.js').then(value=>{completed=true;return value});
        await Promise.resolve(); assert.equal(completed,false); assert.equal(timers.length,1);
        assert.equal(await worklet.addModule('/unrelated', {credentials:'omit'}),'native-result');
        assert.equal(timers.length,1);
        assert.deepEqual(calls,[['/assets/machine-speech.worklet.js'],['/unrelated',{credentials:'omit'}]]);
        timers[0](); assert.equal(await pending,'native-result'); assert.equal(completed,true);
      })().catch(()=>{process.exitCode=1});"""
    result = subprocess.run(["node", "-e", code, SCRIPT], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, "native worklet setup delay fixture failed"
