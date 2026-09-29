"""Execute the actual isolated bridge JavaScript against deterministic source races."""

import json
import shutil
import subprocess

import pytest

from worker.meet_media.avatar_browser import _CLOSE, _PULSE, _START, _STATE


def test_actual_bridge_fences_late_setup_busy_owner_and_stale_pulses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("bounded bridge-JavaScript test requires local Node")
    operations = json.dumps({"start": _START, "state": _STATE, "pulse": _PULSE, "close": _CLOSE})
    script = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const operations = JSON.parse(process.argv[1]);
let generation = 0, active = false, finish;
const closed = [], pulses = [];
const source = {
  status: () => ({generation}),
  open(id, profile, image) {
    if (id === 'avatar:synthetic-image') {
      assert.equal(profile, 'persona-image-v1');
      assert.equal(image.png, 'bounded-fixture'); assert.equal(image.sha256, 'a'.repeat(64));
    } else { assert.equal(id, 'avatar:synthetic'); assert.equal(profile, 'neutral-ai-v1'); }
    if (active) return Promise.reject(new Error('busy'));
    active = true; generation++;
    return new Promise(resolve => { finish = resolve; });
  },
  pulse(gen) { assert.equal(gen, generation); pulses.push(gen); },
  close(gen) { closed.push(gen); if (gen === generation) active = false; },
};
const context = vm.createContext({window: {anantaMachine: {avatar: source}}, Promise});
const run = (name, arg) => vm.runInContext('(' + operations[name] + ')', context)(arg);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  run('start', ['old', 'avatar:synthetic']); const oldFinish = finish;
  run('close', 'old'); run('start', ['fresh', 'avatar:synthetic']);
  oldFinish({generation: 1}); await flush();
  assert.equal(run('state', 'fresh').phase, 'pending');
  run('close', 'old'); assert.deepEqual(closed, [1]);
  assert.throws(() => run('pulse', 'old'), /stale/);
  run('pulse', 'fresh'); assert.deepEqual(pulses, [2]);
  finish({generation: 2}); await flush(); assert.equal(run('state', 'fresh').phase, 'done');
  run('close', 'fresh'); assert.deepEqual(closed, [1, 2]);

  // A rejected busy open must not acquire the pre-existing source generation.
  active = true; run('start', ['busy', 'avatar:synthetic']); await flush();
  assert.equal(run('state', 'busy').generation, null);
  assert.equal(run('state', 'busy').phase, 'failed');
  run('close', 'busy'); assert.deepEqual(closed, [1, 2]); assert.equal(active, true);

  active = false; run('start', ['oversize', 'avatar:synthetic']);
  finish({data: 'x'.repeat(1025)}); await flush();
  assert.equal(run('state', 'oversize').phase, 'failed');
  run('close', 'oversize'); assert.deepEqual(closed, [1, 2, 3]);

  run('start', ['image', 'avatar:synthetic-image', 'persona-image-v1',
    {png: 'bounded-fixture', sha256: 'a'.repeat(64)}]);
  const imageFinish = finish;
  run('close', 'image'); run('start', ['replacement', 'avatar:synthetic']);
  imageFinish({generation: 4, profile: 'persona-image-v1'}); await flush();
  assert.equal(run('state', 'replacement').phase, 'pending');
  run('close', 'image'); assert.deepEqual(closed, [1, 2, 3, 4]);
  finish({generation: 5, profile: 'neutral-ai-v1'}); await flush();
  assert.equal(run('state', 'replacement').receipt.profile, 'neutral-ai-v1');
  run('close', 'replacement'); assert.deepEqual(closed, [1, 2, 3, 4, 5]);
  process.stdout.write('bridge-races-ok');
})().catch(error => { process.stderr.write(error.name); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script, operations], capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "bridge-races-ok"
