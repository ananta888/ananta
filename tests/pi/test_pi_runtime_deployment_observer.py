"""Synthetic bounded Docker observations; no productive deployment attestation."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.pi_docker_runtime_observer import DockerPiRuntimeObserver
from agent.services.pi_runtime_deployments import PiDeploymentBinding, PinnedPiRuntimeDeployments
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


@pytest.fixture
def binding():
    manifest = PiRuntimeManifest(tenant_id='test-tenant', project_id='test-project', worker_id='test-worker',
        worker_url='http://worker:5000', repository_revision='a' * 40, image_digest='sha256:' + 'b' * 64,
        node_version='24.18.0', pi_version='0.85.1', evidence_scope='test', synthetic=True)
    return PiDeploymentBinding(manifest=manifest, container_id='c' * 64, node_binary='/opt/node/bin/node',
        package_json='/opt/pi/node_modules/@earendil-works/pi-coding-agent/package.json', user_id='1000:1000')


def runner_for(binding, change='none'):
    calls = []

    def run(args, *, timeout_seconds):
        assert timeout_seconds == 5
        calls.append(args)
        if change == 'error':
            raise RuntimeError('private synthetic credential')
        if args[1] == 'inspect':
            state = [binding.container_id, binding.manifest.image_digest, True, True, 'synthetic-start']
            if change == 'stopped':
                state[2] = False
            elif change == 'image':
                state[1] = 'sha256:' + 'd' * 64
            elif change == 'writable':
                state[3] = False
            elif change == 'restart' and len(calls) > 1:
                state[4] = 'different-start'
            elif change in {'mounted', 'symlink'}:
                state.append('/mutable')
                if change == 'mounted':
                    state[-1] = '/opt/pi'
            elif change == 'library':
                state.append('/usr/local/lib/python3.12/json')
            output = '\n'.join(json.dumps(value) for value in state) + '\n'
        elif args[1] == 'image':
            revision = 'd' * 40 if change == 'revision' else binding.manifest.repository_revision
            output = json.dumps(binding.manifest.image_digest) + '\n' + json.dumps(revision)
        elif '--version' in args:
            output = 'v24.19.0' if change == 'node' else 'v' + binding.manifest.node_version
        else:
            package = ['@earendil-works/pi-coding-agent', binding.manifest.pi_version,
                binding.package_json, binding.node_binary]
            if change == 'package':
                package[1] = '0.84.0'
            elif change == 'symlink':
                package[2] = '/mutable/package.json'
            output = json.dumps(package)
        return SimpleNamespace(returncode=1 if change == 'nonzero' else 0,
            truncated=change == 'truncated', stdout=output)

    return SimpleNamespace(run=run, calls=calls)


def test_pinned_inventory_checks_full_independent_observation(binding):
    runner = runner_for(binding)
    deployments = PinnedPiRuntimeDeployments(bindings=[binding], observer=DockerPiRuntimeObserver(runner=runner))
    identity = dict(tenant_id=binding.manifest.tenant_id, project_id=binding.manifest.project_id,
        worker_id=binding.manifest.worker_id, worker_url=binding.manifest.worker_url)
    assert deployments.require_manifest(**identity) == binding.manifest
    assert len(runner.calls) == 5
    execs = [call for call in runner.calls if call[1] == 'exec']
    assert all(call[2:6] == ['--user', '1000:1000', '--workdir', '/'] for call in execs)
    assert all('/usr/bin/env' in call and '-i' in call for call in execs)
    with pytest.raises(ValueError, match='not_authorized'):
        deployments.require_manifest(**{**identity, 'project_id': 'foreign'})
    assert len(runner.calls) == 5


@pytest.mark.parametrize('change', [
    'stopped', 'image', 'writable', 'restart', 'mounted', 'symlink', 'library',
    'revision', 'node', 'package', 'truncated', 'nonzero', 'error'])
def test_runtime_mismatch_or_probe_failure_cannot_admit_manifest(binding, change):
    with pytest.raises(ValueError) as denied:
        DockerPiRuntimeObserver(runner=runner_for(binding, change)).observe(binding)
    assert 'private synthetic credential' not in str(denied.value)


@pytest.mark.parametrize('field,value', [('container_id', 'short'), ('user_id', '0:0'),
    ('node_binary', '/opt/../tmp/node'), ('package_json', '/opt/pi/file\n')])
def test_inventory_requires_exact_container_and_closed_runtime_paths(binding, field, value):
    with pytest.raises(ValueError):
        replace(binding, **{field: value})


def test_duplicate_inventory_binding_rejected(binding):
    observer = Mock()
    with pytest.raises(ValueError, match='inventory_invalid'):
        PinnedPiRuntimeDeployments(bindings=[binding, binding], observer=observer)
    observer.observe.assert_not_called()


def test_docker_inspect_terminal_blank_line_is_valid_without_ignoring_middle_data(binding):
    runner = runner_for(binding)
    original = runner.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if args[1] == 'inspect':
            result.stdout += '\n'
        return result

    runner.run = run
    assert DockerPiRuntimeObserver(runner=runner).observe(binding).running is True
