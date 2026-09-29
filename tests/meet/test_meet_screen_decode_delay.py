"""The delay fixture preserves native decoding, bitmap identity and non-JPEG work."""

import subprocess

from tests.meet_screen_decode_delay import SCRIPT


def test_exact_script_delays_only_native_jpeg_completion():
    script = """const assert=require('node:assert/strict');
      let timers=[], calls=[], bitmap={synthetic:true};
      global.window={createImageBitmap(...args){calls.push(args);return Promise.resolve(bitmap)}};
      global.setTimeout=(callback,delay)=>{assert.equal(delay,300);timers.push(callback)};
      new Function(process.argv[1])();
      (async()=>{
        const jpeg=new Blob(['synthetic'],{type:'image/jpeg'}), png=new Blob([],{type:'image/png'});
        let done=false;
        const pending=window.createImageBitmap(jpeg).then(value=>{done=true;return value});
        await Promise.resolve();assert.equal(done,false);
        assert.equal(await window.createImageBitmap(png,{premultiplyAlpha:'none'}),bitmap);
        assert.equal(timers.length,1);assert.equal(calls[0][0],jpeg);assert.equal(calls[1][0],png);
        assert.deepEqual(calls[1][1],{premultiplyAlpha:'none'});
        timers[0]();assert.equal(await pending,bitmap);assert.equal(done,true);
      })().catch(e=>{console.error(e);process.exitCode=1});"""
    result = subprocess.run(["node", "-e", script, SCRIPT], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
