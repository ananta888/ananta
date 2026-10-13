"""Narrow read-only Docker probes of explicit Hub-authorized Pi containers."""

import json
from pathlib import PurePosixPath

from agent.services.pi_runtime_deployments import PiDeploymentBinding, PiRuntimeObservation

# No environment, credentials, mount sources, prompts or model output are read.
_CONTAINER_FORMAT = '{{json .Id}}\n{{json .Image}}\n{{json .State.Running}}\n' \
    '{{json .HostConfig.ReadonlyRootfs}}\n{{json .State.StartedAt}}\n' \
    '{{range .Mounts}}{{json .Destination}}\n{{end}}'
_IMAGE_FORMAT = '{{json .Id}}\n{{json (index .Config.Labels "org.opencontainers.image.revision")}}'
_PACKAGE_PROBE = 'import json,os,sys; p=json.load(open(sys.argv[1])); ' \
    'print(json.dumps([p.get("name"),p.get("version"),os.path.realpath(sys.argv[1]),os.path.realpath(sys.argv[2])]))'


class DockerPiRuntimeObserver:
    def __init__(self, *, runner):
        # The caller supplies the pre-authorized Hub Docker capability.
        self._runner = runner

    def _run(self, args):
        try:
            result = self._runner.run(args, timeout_seconds=5)
        except Exception:
            raise ValueError('pi_deployment_probe_failed') from None
        if result.returncode != 0 or result.truncated or len(result.stdout.encode()) > 16384:
            raise ValueError('pi_deployment_probe_failed')
        return result.stdout

    def _container(self, binding):
        raw = self._run(['docker', 'inspect', '--format', _CONTAINER_FORMAT, binding.container_id])
        try:
            values = [json.loads(line) for line in raw.rstrip('\r\n').splitlines()]
        except (ValueError, TypeError) as exc:
            raise ValueError('pi_deployment_probe_invalid') from exc
        if (len(values) < 5 or len(values) > 133 or values[0] != binding.container_id
                or values[1] != binding.manifest.image_digest or values[2] is not True or values[3] is not True
                or not isinstance(values[4], str) or not values[4]
                or any(not isinstance(path, str) or not path.startswith('/') for path in values[5:])):
            raise ValueError('pi_deployment_container_mismatch')
        return values

    @staticmethod
    def _unmounted(paths, mounts):
        for value in paths:
            path = PurePosixPath(value)
            if not path.is_absolute():
                raise ValueError('pi_deployment_runtime_path_invalid')
            for mount in mounts:
                root = PurePosixPath(mount)
                if path == root or root in path.parents:
                    raise ValueError('pi_deployment_mutable_runtime_denied')

    def observe(self, binding: PiDeploymentBinding):
        before = self._container(binding)
        paths = [binding.node_binary, binding.package_json, '/usr/local/bin/python', '/usr/bin/env']
        self._unmounted(paths, before[5:])
        for mount in before[5:]:
            destination = PurePosixPath(mount)
            for value in ('/usr/local/lib', '/usr/lib', '/lib', '/lib64'):
                library = PurePosixPath(value)
                if (library == destination or library in destination.parents or destination in library.parents):
                    raise ValueError('pi_deployment_mutable_runtime_denied')
        try:
            image = [json.loads(line) for line in self._run([
                'docker', 'image', 'inspect', '--format', _IMAGE_FORMAT, binding.manifest.image_digest]).splitlines()]
        except (ValueError, TypeError) as exc:
            raise ValueError('pi_deployment_probe_invalid') from exc
        if image != [binding.manifest.image_digest, binding.manifest.repository_revision]:
            raise ValueError('pi_deployment_image_revision_mismatch')
        prefix = ['docker', 'exec', '--user', binding.user_id, '--workdir', '/', binding.container_id,
                  '/usr/bin/env', '-i']
        node = self._run([*prefix, binding.node_binary, '--version']).strip()
        try:
            package = json.loads(self._run([*prefix, '/usr/local/bin/python', '-I', '-S', '-c', _PACKAGE_PROBE,
                binding.package_json, binding.node_binary]))
        except (ValueError, TypeError) as exc:
            raise ValueError('pi_deployment_probe_invalid') from exc
        if (not isinstance(package, list) or len(package) != 4
                or package[:2] != ['@earendil-works/pi-coding-agent', binding.manifest.pi_version]
                or any(not isinstance(value, str) for value in package)):
            raise ValueError('pi_deployment_package_mismatch')
        self._unmounted(package[2:], before[5:])
        if self._container(binding) != before:
            raise ValueError('pi_deployment_changed_during_probe')
        if node != 'v' + binding.manifest.node_version:
            raise ValueError('pi_deployment_node_version_mismatch')
        return PiRuntimeObservation(container_id=binding.container_id, image_digest=before[1],
            repository_revision=image[1], node_version=node[1:], pi_version=package[1],
            running=True, readonly_rootfs=True)
