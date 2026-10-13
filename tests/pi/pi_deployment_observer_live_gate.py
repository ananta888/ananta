"""Owned Docker fixture gate; technical observation, never production evidence."""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from agent.services.ops_command_runner import CommandRunner
from agent.services.pi_docker_runtime_observer import DockerPiRuntimeObserver
from agent.services.pi_runtime_deployments import PiDeploymentBinding
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


def run_gate(*, node, modules, base, revision):
    if (re.fullmatch(r'sha256:[a-f0-9]{64}', base) is None
            or re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', revision) is None):
        raise ValueError('pi_observer_fixture_pins_required')
    containers = []
    image = None
    alias = "ananta-pi-observer-base:" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='ananta-pi-observer-') as directory:
        root = Path(directory)
        shutil.copy2(node, root / 'node')
        shutil.copytree(modules, root / 'node_modules', symlinks=True)
        (root / 'Dockerfile').write_text(
            f'FROM {alias}\nLABEL org.opencontainers.image.revision="{revision}"\n'
            'COPY node /opt/node/bin/node\nCOPY node_modules /opt/pi/node_modules\n'
            'USER 1000:1000\nCMD ["/usr/local/bin/python", "-I", "-S", "-c", "import time;time.sleep(180)"]\n')
        runner = CommandRunner(max_output_bytes=16384)
        try:
            subprocess.run(['docker', 'image', 'tag', base, alias], check=True, capture_output=True, timeout=10)
            with (root / 'build.log').open('w') as log:
                result = subprocess.run(['docker', 'build', '--network=none', '--iidfile', str(root / 'iid'),
                    str(root)], stdout=log, stderr=subprocess.STDOUT, timeout=180)
            if result.returncode:
                shutil.copy2(root / 'build.log', '/tmp/ananta-pi-observer-build-failure.log')
                raise ValueError('pi_observer_fixture_build_failed')
            image = (root / 'iid').read_text().strip()
            result = runner.run(['docker', 'run', '-d', '--read-only', '--network=none', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=512m', '--cpus=1', image])
            if result.returncode:
                raise ValueError('pi_observer_fixture_start_failed')
            container = result.stdout.strip()
            containers.append(container)
            manifest = PiRuntimeManifest(tenant_id='observer-test', project_id='observer-test',
                worker_id='observer-test',
                worker_url='http://observer-test:5000', repository_revision=revision, image_digest=image,
                node_version='24.21.0', pi_version='0.85.1', evidence_scope='test', synthetic=True)
            binding = PiDeploymentBinding(manifest=manifest, container_id=container, node_binary='/opt/node/bin/node',
                package_json='/opt/pi/node_modules/@earendil-works/pi-coding-agent/package.json', user_id='1000:1000')
            observation = DockerPiRuntimeObserver(runner=runner).observe(binding)
            if observation.node_version != manifest.node_version or observation.pi_version != manifest.pi_version:
                raise ValueError('pi_observer_fixture_observation_failed')
            result = runner.run(['docker', 'run', '-d', '--read-only', '--network=none', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=512m', '--cpus=1',
                '--mount', f'type=bind,src={root / "node_modules"},dst=/opt/pi/node_modules,readonly', image])
            if result.returncode:
                raise ValueError('pi_observer_mutable_fixture_start_failed')
            other = result.stdout.strip()
            containers.append(other)
            try:
                DockerPiRuntimeObserver(runner=runner).observe(replace(binding, container_id=other))
            except ValueError as exc:
                if str(exc) != 'pi_deployment_mutable_runtime_denied':
                    raise
            else:
                raise ValueError('pi_observer_mutable_runtime_wrongly_admitted')
            return {'classification': 'local_isolated_docker_technical_observation',
                'production_release_evidence': False, 'actual_docker_probe': True,
                'actual_mutable_runtime_rejected': True,
                'node_version': observation.node_version,
                'pi_version': observation.pi_version, 'readonly_rootfs': observation.readonly_rootfs}
        finally:
            cleanup_failed = False
            for container in containers:
                cleanup_failed |= runner.run(['docker', 'rm', '-f', container]).returncode != 0
            if image:
                cleanup_failed |= runner.run(['docker', 'image', 'rm', image]).returncode != 0
            cleanup_failed |= runner.run(['docker', 'image', 'rm', alias]).returncode != 0
            if cleanup_failed:
                raise ValueError('pi_observer_fixture_cleanup_failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('node', 'modules', 'base', 'revision'):
        parser.add_argument('--' + name, required=True)
    values = vars(parser.parse_args())
    print(json.dumps(run_gate(**values), sort_keys=True))
