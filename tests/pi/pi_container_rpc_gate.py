"""Bounded negative RPC checks between two owned application containers."""

import argparse
import json
import re
import subprocess
import uuid

_SERVER = '''from agent.database import init_db
init_db()
from agent.ai_agent import create_app
app=create_app(testing=True)
app.config['AGENT_TOKEN']='synthetic-pi-rpc-fixture-token-1234567890'
app.config['AGENT_TOKEN_FILE']=None
app.run(host='0.0.0.0',port=5000,use_reloader=False)
'''
_CLIENT = '''import json,time,urllib.request,urllib.error
target=__import__('sys').argv[1]
authorized=__import__('sys').argv[2]=='yes'
headers={'Content-Type':'application/json'}
if authorized: headers['Authorization']='Bearer synthetic-pi-rpc-fixture-token-1234567890'
deadline=time.monotonic()+30
while True:
 try:
  request=urllib.request.Request('http://'+target+':5000/api/internal/native-tasks/execute',data=b'{}',headers=headers)
  try:
   response=urllib.request.urlopen(request,timeout=2)
  except urllib.error.HTTPError as error:
   response=error
  body=response.read(4096)
  code=response.code
  reason=json.loads(body).get('reason_code') if code!=404 else None
  print(json.dumps({'status':code,'reason_code':reason}));break
 except (OSError,ValueError):
  if time.monotonic()>=deadline: raise SystemExit('pi_rpc_readiness_timeout')
  time.sleep(.25)
'''


def _run(arguments, *, timeout=45):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('pi_container_rpc_command_failed')
    return result.stdout.strip()


def probe(image):
    if re.fullmatch(r'sha256:[a-f0-9]{64}', image) is None:
        raise ValueError('pi_rpc_image_digest_required')
    prefix = 'ananta-pi-rpc-' + uuid.uuid4().hex
    network = prefix + '-net'
    containers = [prefix + '-hub', prefix + '-worker']
    try:
        _run(['docker', 'network', 'create', '--internal', network])
        for role, name in zip(('hub', 'worker'), containers, strict=True):
            _run(['docker', 'run', '-d', '--name', name, '--network', network,
                  '--network-alias', role, '--read-only', '--cap-drop=ALL',
                  '--security-opt=no-new-privileges', '--pids-limit=128',
                  '--memory=1536m', '--cpus=2', '--tmpfs',
                  '/tmp:rw,nosuid,nodev,noexec,size=192m,uid=1000,gid=1000,mode=0700',
                  '-e', 'DATA_DIR=/tmp/ananta-data',
                  '-e', 'DATABASE_URL=sqlite:////tmp/ananta-data/rpc.sqlite3',
                  '-e', 'ROLE=' + role,
                  '-e', 'SECRET_KEY=synthetic-pi-rpc-secret-1234567890',
                  '--entrypoint', '/usr/local/bin/python', image, '-c', _SERVER])
        checks = {}
        for source, target, authorized, key, expected in (
            (containers[0], 'worker', 'no', 'worker_requires_auth',
             {'status': 401, 'reason_code': 'native_execution_auth_required'}),
            (containers[0], 'worker', 'yes', 'worker_requires_signature',
             {'status': 422, 'reason_code': 'native_execution_projection_denied'}),
            (containers[1], 'hub', 'yes', 'hub_has_no_worker_execution_route',
             {'status': 404, 'reason_code': None}),
        ):
            observed = json.loads(_run(['docker', 'exec', source, '/usr/local/bin/python',
                                        '-c', _CLIENT, target, authorized]))
            if observed != expected:
                raise ValueError('pi_container_rpc_boundary_failed:' + key)
            checks[key] = observed
        return {'classification': 'owned_two_container_negative_rpc_technical_observation',
                'production_release_evidence': False, 'image': image, 'checks': checks}
    finally:
        for name in containers:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    print(json.dumps(probe(parser.parse_args().image), sort_keys=True))
