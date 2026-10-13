"""Run bounded PostgreSQL concurrency tests in an owned temporary database."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree


def command(args, *, timeout=15):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('bpmn_postgres_fixture_command_failed')
    return result.stdout.strip()


def run(image):
    if re.fullmatch(r'sha256:[a-f0-9]{64}', image) is None:
        raise ValueError('bpmn_postgres_image_digest_required')
    name = 'ananta-bpmn-pg-' + uuid.uuid4().hex
    network = name + '-net'
    root = Path(__file__).resolve().parents[1]
    try:
        command(['docker', 'network', 'create', network])
        command(['docker', 'run', '-d', '--name', name, '--network', network,
            '--publish', '127.0.0.1::5432', '--user', '999:999', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=768m', '--cpus=2',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=512m,uid=999,gid=999,mode=0700',
            '--tmpfs', '/var/run/postgresql:rw,nosuid,nodev,noexec,size=16m,uid=999,gid=999,mode=0755',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,uid=999,gid=999,mode=0700',
            '-e', 'POSTGRES_USER=ananta_fixture', '-e', 'POSTGRES_DB=ananta_bpmn_fixture',
            '-e', 'POSTGRES_PASSWORD=synthetic-postgres-fixture-password', image,
            'postgres', '-c', 'shared_buffers=32MB', '-c', 'min_wal_size=32MB',
            '-c', 'max_wal_size=64MB', '-c', 'checkpoint_timeout=30s'])
        deadline = time.monotonic() + 30
        while True:
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'ananta_fixture',
                                    '-d', 'ananta_bpmn_fixture'], capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            state = json.loads(command(['docker', 'inspect', '--format', '{{json .State}}', name]))
            if not state['Running'] or time.monotonic() >= deadline:
                raise ValueError('bpmn_postgres_fixture_readiness_failed')
            time.sleep(.25)
        ports = json.loads(command(['docker', 'inspect', '--format',
                                    '{{json (index .NetworkSettings.Ports "5432/tcp")}}', name]))
        if (not isinstance(ports, list) or len(ports) != 1
                or ports[0]['HostIp'] != '127.0.0.1' or not ports[0]['HostPort'].isdigit()):
            raise ValueError('bpmn_postgres_fixture_port_invalid')
        env = {**os.environ, 'ANANTA_TEST_NATIVE_POSTGRES_URL':
            'postgresql+psycopg2://ananta_fixture:synthetic-postgres-fixture-password@127.0.0.1:'
            + ports[0]['HostPort'] + '/ananta_bpmn_fixture'}
        with TemporaryDirectory(prefix='ananta-bpmn-postgres-report-') as directory:
            report = Path(directory) / 'pytest.xml'
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                '--junitxml=' + str(report), 'tests/bpmn/test_native_postgres_concurrency.py',
                'tests/bpmn/test_event_wait_postgres.py', 'tests/bpmn/test_native_maintenance_postgres.py',
                'tests/bpmn/test_native_retirement_postgres.py'],
                cwd=root, env=env,
                capture_output=True, text=True, timeout=90)
            Path('/tmp/ananta-bpmn-postgres-tests.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError('bpmn_postgres_concurrency_tests_failed')
            suites = ElementTree.parse(report).getroot().findall('testsuite')
            counts = {name: sum(int(suite.get(name, '0')) for suite in suites)
                      for name in ('tests', 'failures', 'errors', 'skipped')}
            if counts != {'tests': 13, 'failures': 0, 'errors': 0, 'skipped': 0}:
                raise ValueError('bpmn_postgres_test_scope_incomplete')
        return {'classification': 'owned_postgres_native_concurrency_technical_observation',
            'production_release_evidence': False, 'postgres_image': image, 'tests_passed': counts['tests'],
            'process_locks_shared': False, 'postgres_advisory_locks_exercised': True}
    finally:
        diagnostics = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, timeout=10)
        Path('/tmp/ananta-bpmn-postgres-container.log').write_text(diagnostics.stdout + diagnostics.stderr)
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    print(json.dumps(run(parser.parse_args().image), sort_keys=True))
