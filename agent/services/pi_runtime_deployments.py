"""Hub-pinned deployment inventory checked against independent runtime observations."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Protocol

from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


@dataclass(frozen=True)
class PiDeploymentBinding:
    manifest: PiRuntimeManifest
    container_id: str
    node_binary: str
    package_json: str
    user_id: str

    def __post_init__(self):
        if re.fullmatch(r'[a-f0-9]{64}', self.container_id) is None:
            raise ValueError('pi_deployment_container_identity_required')
        if re.fullmatch(r'[1-9][0-9]{0,8}:[1-9][0-9]{0,8}', self.user_id) is None:
            raise ValueError('pi_deployment_nonroot_user_required')
        for value in (self.node_binary, self.package_json):
            path = PurePosixPath(value)
            if (not path.is_absolute() or str(path) != value or '..' in path.parts
                    or len(value) > 1024 or any(ord(c) < 33 for c in value)):
                raise ValueError('pi_deployment_runtime_path_invalid')


@dataclass(frozen=True)
class PiRuntimeObservation:
    container_id: str
    image_digest: str
    repository_revision: str
    node_version: str
    pi_version: str
    running: bool
    readonly_rootfs: bool


class PiRuntimeObserverPort(Protocol):
    def observe(self, binding: PiDeploymentBinding) -> PiRuntimeObservation: ...


def _identity(manifest):
    return manifest.tenant_id, manifest.project_id, manifest.worker_id, manifest.worker_url


class PinnedPiRuntimeDeployments:
    def __init__(self, *, bindings, observer: PiRuntimeObserverPort):
        inventory = {}
        for binding in bindings:
            if not isinstance(binding, PiDeploymentBinding) or _identity(binding.manifest) in inventory:
                raise ValueError('pi_deployment_inventory_invalid')
            inventory[_identity(binding.manifest)] = binding
        self._inventory, self._observer = MappingProxyType(inventory), observer

    def require_manifest(self, *, tenant_id, project_id, worker_id, worker_url):
        binding = self._inventory.get((tenant_id, project_id, worker_id, worker_url))
        if binding is None:
            raise ValueError('pi_deployment_not_authorized')
        observed = self._observer.observe(binding)
        expected = PiRuntimeObservation(container_id=binding.container_id,
            image_digest=binding.manifest.image_digest, repository_revision=binding.manifest.repository_revision,
            node_version=binding.manifest.node_version, pi_version=binding.manifest.pi_version,
            running=True, readonly_rootfs=True)
        if (not isinstance(observed, PiRuntimeObservation) or observed != expected
                or observed.running is not True or observed.readonly_rootfs is not True):
            raise ValueError('pi_deployment_observation_mismatch')
        return binding.manifest
