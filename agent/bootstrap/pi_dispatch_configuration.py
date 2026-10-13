"""Closed trusted Hub configuration for Pi deployment and policy adapters."""

from agent.services.pi_evidence_policy import PiEvidencePolicyPin, PinnedPiEvidencePolicy
from agent.services.pi_runtime_deployments import PiDeploymentBinding, PinnedPiRuntimeDeployments
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest

_FIELDS = {'manifest', 'container_id', 'node_binary', 'package_json', 'user_id', 'policy'}
_POLICY_FIELDS = {'policy_version', 'provider_profile_id', 'provider_id', 'model_id'}


def compose_pi_dispatch_ports(raw, *, observer):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
        raise ValueError('pi_dispatch_inventory_configuration_invalid')
    bindings, pins = [], []
    try:
        for item in raw:
            if not isinstance(item, dict) or set(item) != _FIELDS:
                raise ValueError('fields')
            policy = item['policy']
            if (not isinstance(policy, dict) or not _POLICY_FIELDS <= set(policy)
                    or set(policy) - _POLICY_FIELDS - {'preauthorized_read_only', 'preauthorized_result_artifacts'}):
                raise ValueError('policy')
            manifest = PiRuntimeManifest.from_mapping(item['manifest'])
            bindings.append(PiDeploymentBinding(manifest=manifest,
                **{key: item[key] for key in ('container_id', 'node_binary', 'package_json', 'user_id')}))
            pins.append(PiEvidencePolicyPin(manifest=manifest, **policy))
        deployments = PinnedPiRuntimeDeployments(bindings=bindings, observer=observer)
        return deployments, PinnedPiEvidencePolicy(pins=pins, deployments=deployments)
    except (KeyError, TypeError, ValueError):
        raise ValueError('pi_dispatch_inventory_configuration_invalid') from None


def production_pi_runtime_observer():
    from agent.services.ops_command_runner import CommandRunner
    from agent.services.pi_docker_runtime_observer import DockerPiRuntimeObserver

    return DockerPiRuntimeObserver(runner=CommandRunner(max_output_bytes=16384))
