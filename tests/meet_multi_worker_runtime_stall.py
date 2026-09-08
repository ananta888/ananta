"""Freeze only one owned runtime; verify its real browser descendants disappear."""

import json

from tests.meet_multi_worker_crash import owned_worker_id

STALL_PROBE = r"""
import json, os, signal, time
from pathlib import Path

def processes():
    rows = {}
    names = [p for p in Path('/proc').iterdir() if p.name.isdecimal()]
    assert len(names) <= 256, 'test process scope exceeded'
    for path in names:
        try:
            fields = (path / 'stat').read_text().rpartition(')')[2].split()
            raw = (path / 'cmdline').open('rb').read(4097)
        except FileNotFoundError:
            continue
        assert len(raw) <= 4096, 'test process metadata excessive'
        args = raw.rstrip(bytes([0])).split(bytes([0]))
        rows[int(path.name)] = {'state': fields[0], 'parent': int(fields[1]),
            'group': int(fields[2]), 'start': fields[19], 'args': args}
    return rows

before = processes()
runtime = [pid for pid, row in before.items()
    if row['args'][-2:] == [b'-m', b'worker.meet_media.dialog_runtime']]
assert len(runtime) == 1, 'one assigned runtime required'
pid = runtime[0]
assert before[pid]['group'] == pid and pid != os.getpid(), 'owned process group required'
owned = {pid}
for _ in range(32):
    children = {key for key, row in before.items() if row['parent'] in owned}
    if children <= owned:
        break
    owned |= children
else:
    raise AssertionError('test process tree depth exceeded')
assert len(owned) >= 3, 'actual browser descendants required'
current = processes()[pid]
assert current['start'] == before[pid]['start'], 'runtime identity changed'
began = time.monotonic()
os.kill(pid, signal.SIGSTOP)
remaining = []
while time.monotonic() - began < 4:
    after = processes()
    remaining = [key for key in owned if key in after and after[key]['start'] == before[key]['start']
        and after[key]['state'] != 'Z']
    if not remaining:
        break
    time.sleep(0.025)
assert not remaining, 'stalled runtime or owned browser descendants survived watchdog'
browser_names = {b'chrome', b'chrome_crashpad_handler', b'headless_shell', b'node'}
assert not any(row['state'] != 'Z' and os.path.basename(row['args'][0]) in browser_names
    for row in processes().values()), 'untracked browser process survived'
print(json.dumps({'runtime_frozen': True, 'known_descendants': len(owned) - 1,
    'active_descendants_after': 0, 'stop_ms': round((time.monotonic() - began) * 1000, 2)}))
"""


def stall_owned_runtime(container, record_property):
    identifier = owned_worker_id(container)
    raw = container.command("exec", identifier, "python", "-S", "-c", STALL_PROBE)
    assert len(raw) <= 512, "runtime stall result excessive"
    result = json.loads(raw)
    assert set(result) == {"runtime_frozen", "known_descendants", "active_descendants_after", "stop_ms"}
    assert result["runtime_frozen"] is True
    assert type(result["active_descendants_after"]) is int and result["active_descendants_after"] == 0
    assert type(result["known_descendants"]) is int and 2 <= result["known_descendants"] < 256
    assert type(result["stop_ms"]) in {int, float} and 0 < result["stop_ms"] < 4000
    state = json.loads(container.command("inspect", identifier, "--format", "{{json .State}}"))
    assert state["Running"] is True, "resource watchdog stopped the whole Worker container"
    assert container.command("exec", identifier, "python", "-S", "-m", "worker.meet_media.health") == ""
    record_property(
        "packaged_runtime_stall",
        result | {"container_remains_live": True, "synthetic_policy": True, "production_release_evidence": False},
    )
