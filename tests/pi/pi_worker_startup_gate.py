"""Run a bounded owned Worker app-startup container; never touch serving deployments."""

import argparse
import json
import subprocess
import uuid

_CODE = '''import json
from agent.database import init_db
init_db()
from agent.ai_agent import create_app
app=create_app(testing=True)
routes={rule.rule for rule in app.url_map.iter_rules()}
print('PI_STARTUP_RESULT='+json.dumps({
 'role':app.config.get('ROLE'),
 'native_execute_route':'/api/internal/native-tasks/execute' in routes,
 'native_result_route':'/api/internal/native-tasks/result' in routes}))
'''


def probe(image, *, role='worker'):
    import re

    if re.fullmatch(r'sha256:[a-f0-9]{64}', image) is None:
        raise ValueError('pi_worker_startup_image_digest_required')
    if role not in {'hub', 'worker'}:
        raise ValueError('pi_startup_role_invalid')
    name = 'ananta-pi-startup-' + uuid.uuid4().hex
    args = ['docker', 'run', '--name', name, '--rm', '--read-only', '--network=none', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=1536m', '--cpus=2',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=192m,uid=1000,gid=1000,mode=0700',
        '-e', 'DATA_DIR=/tmp/ananta-data', '-e', 'DATABASE_URL=sqlite:////tmp/ananta-data/worker.sqlite3',
        '-e', 'ROLE=' + role,
        '-e', 'SECRET_KEY=synthetic-worker-startup-secret-1234567890',
        '--entrypoint', '/usr/local/bin/python', image, '-c', _CODE]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
    markers = [line for line in result.stdout.splitlines() if line.startswith('PI_STARTUP_RESULT=')]
    if result.returncode or len(markers) != 1:
        # Store owned test diagnostics locally; never emit config/error content.
        from pathlib import Path

        Path('/tmp/ananta-pi-worker-app-startup.log').write_text(result.stdout + result.stderr)
        raise ValueError('pi_worker_app_startup_failed')
    observed = json.loads(markers[0].split('=', 1)[1])
    if observed != {'role': role, 'native_execute_route': role == 'worker', 'native_result_route': role == 'worker'}:
        raise ValueError('pi_worker_app_startup_routes_invalid')
    return {'classification': 'local_owned_container_app_startup_technical_observation',
            'production_release_evidence': False, **observed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--role', choices=('hub', 'worker'), default='worker')
    args = parser.parse_args()
    print(json.dumps(probe(args.image, role=args.role), sort_keys=True))
