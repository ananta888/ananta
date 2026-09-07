"""Execute the exact diagnostic script against deterministic event ports."""

import subprocess

from tests.meet_speech_worklet_observation import INSTALL


def test_observer_forwards_construction_and_only_retains_bounded_known_error_codes():
    script = """
      const assert = require('node:assert/strict');
      class Native {
        constructor(...args) { this.args = args; this.port = new EventTarget(); }
      }
      global.window = {AudioWorkletNode: Native};
      eval(process.argv[1]);
      const audio = new window.AudioWorkletNode('context', 'ananta-machine-speech-v1', {bound: true});
      const other = new window.AudioWorkletNode('context', 'different');
      assert(audio instanceof Native);
      assert.deepEqual(audio.args, ['context', 'ananta-machine-speech-v1', {bound: true}]);
      let forwarded = 0;
      audio.port.addEventListener('message', () => forwarded++);
      const emit = (node, data) => node.port.dispatchEvent(new MessageEvent('message', {data}));
      emit(other, {type: 'error', code: 'meet_speech_worklet_underrun'});
      emit(audio, {type: 'progress', pcm: 'PRIVATE_DATA'});
      emit(audio, {type: 'error', code: 'PRIVATE_ERROR'});
      emit(audio, {type: 'error', code: {private: 'PRIVATE_ERROR'}});
      assert.deepEqual(window.__testSpeechErrors, []);
      for (let i = 0; i < 12; i++) emit(audio, {type: 'error', code: 'meet_speech_worklet_underrun'});
      assert.deepEqual(window.__testSpeechErrors, Array(8).fill('meet_speech_worklet_underrun'));
      assert.equal(forwarded, 15);
      window = {};
      eval(process.argv[1]);
      assert.deepEqual(window.__testSpeechErrors, []);
      assert.equal(window.AudioWorkletNode, undefined);
    """
    result = subprocess.run(["node", "-e", script, INSTALL], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
