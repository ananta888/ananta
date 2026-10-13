"""Own two isolated containers for the synthetic SQL/SDK acceptance chain."""

import argparse
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from tests.pi.pi_container_rpc_gate import _run


def probe(image, *, mode='normal'):
    if re.fullmatch(r'sha256:[a-f0-9]{64}', image) is None:
        raise ValueError('pi_container_chain_image_digest_required')
    if mode not in {'normal', 'context_mutation', 'answer_artifact'}:
        raise ValueError('pi_container_chain_mode_invalid')
    prefix = 'ananta-pi-chain-' + uuid.uuid4().hex
    network = prefix + '-net'
    names = [prefix + '-hub', prefix + '-worker']
    tests = Path(__file__).resolve().parents[1]
    try:
        _run(['docker', 'network', 'create', '--internal', network])
        for role, name in zip(('hub', 'worker'), names, strict=True):
            _run(['docker', 'run', '-d', '--name', name, '--network', network,
                  '--network-alias', 'hub' if role == 'hub' else 'worker-1',
                  '--network-alias', 'ollama' if role == 'hub' else 'worker',
                  '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                  '--pids-limit=192', '--memory=2048m', '--cpus=2',
                  '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=256m,uid=1000,gid=1000,mode=0700',
                  '--mount', f'type=bind,src={tests},dst=/app/tests,readonly',
                  '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONUNBUFFERED=1',
                  '-e', 'DATA_DIR=/tmp/ananta-data',
                  '-e', 'DATABASE_URL=sqlite:////tmp/ananta-data/app.sqlite3',
                  '-e', 'ROLE=' + role, '-e', 'AGENT_NAME=worker-1',
                  '-e', 'AGENT_URL=http://worker-1:5000',
                  '-e', 'INITIAL_ADMIN_PASSWORD=synthetic-fixture-admin-password-1234567890',
                  '-e', 'ANANTA_TEST_PI_CHAIN_CONTEXT_MUTATION=' + ('1' if mode == 'context_mutation' else '0'),
                  '-e', 'ANANTA_TEST_PI_CHAIN_ANSWER_ARTIFACT=' + ('1' if mode == 'answer_artifact' else '0'),
                  '-e', 'SECRET_KEY=synthetic-pi-chain-secret-1234567890',
                  '--entrypoint', '/usr/local/bin/python', image,
                  '-m', 'tests.pi.pi_container_chain_fixture'])
        deadline = time.monotonic() + 90
        while True:
            state = json.loads(_run(['docker', 'inspect', '--format', '{{json .State}}', names[0]]))
            if not state['Running']:
                break
            worker_state = json.loads(_run(['docker', 'inspect', '--format', '{{json .State}}', names[1]]))
            if not worker_state['Running']:
                raise ValueError('pi_container_chain_worker_stopped')
            if time.monotonic() >= deadline:
                raise ValueError('pi_container_chain_bounded_timeout')
            time.sleep(.5)
        logs = _run(['docker', 'logs', names[0]])
        markers = [line for line in logs.splitlines() if line.startswith('PI_CONTAINER_CHAIN_RESULT=')]
        if state['ExitCode'] or len(markers) != 1:
            raise ValueError('pi_container_chain_hub_failed')
        result = json.loads(markers[0].split('=', 1)[1])
        result['image'] = image
        result['mode'] = mode
        return result
    finally:
        for role, name in zip(('hub', 'worker'), names, strict=True):
            logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, timeout=10)
            Path('/tmp/ananta-pi-chain-' + role + '.log').write_text(logs.stdout + logs.stderr)
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--mode', choices=('normal', 'context_mutation', 'answer_artifact'), default='normal')
    args = parser.parse_args()
    print(json.dumps(probe(args.image, mode=args.mode), sort_keys=True))
