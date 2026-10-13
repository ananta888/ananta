"""Additional Hub-owned preauthorization pins; never bypass current Task authority."""

from dataclasses import asdict, dataclass
from types import MappingProxyType

from agent.ports.pi_runtime_deployment import PiRuntimeDeploymentPort
from agent.services.pi_result_task_projection import pi_task_command
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.pi_result_artifact import pi_answer_artifact
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest
from ananta_contracts.provider_invocation import ProviderInvocationContext


@dataclass(frozen=True)
class PiEvidencePolicyPin:
    manifest: PiRuntimeManifest
    policy_version: str
    provider_profile_id: str
    provider_id: str
    model_id: str
    preauthorized_read_only: bool = False
    preauthorized_result_artifacts: bool = False

    def __post_init__(self):
        if (not isinstance(self.manifest, PiRuntimeManifest) or type(self.preauthorized_read_only) is not bool
                or type(self.preauthorized_result_artifacts) is not bool):
            raise ValueError('pi_evidence_policy_pin_invalid')
        for value in (self.policy_version, self.provider_profile_id, self.provider_id, self.model_id):
            if (not isinstance(value, str) or not 0 < len(value) <= 256
                    or any(ord(char) < 33 for char in value)):
                raise ValueError('pi_evidence_policy_pin_invalid')

    @property
    def digest(self):
        values = asdict(self)
        # Preserve existing no-artifact policy digests.
        if values.pop('preauthorized_result_artifacts'):
            values['preauthorized_result_artifacts'] = True
        return native_context_digest({'schema': 'ananta.pi-evidence-policy.v1', **values,
            'execution_profile': 'pi-sdk-no-tools-v1', 'permission_mode': 'read_only'})


class PinnedPiEvidencePolicy:
    def __init__(self, *, pins, deployments: PiRuntimeDeploymentPort):
        policies = {}
        for pin in pins:
            if not isinstance(pin, PiEvidencePolicyPin):
                raise ValueError('pi_evidence_policy_pin_invalid')
            key = (pin.manifest.tenant_id, pin.manifest.project_id, pin.manifest.worker_url)
            if key in policies:
                raise ValueError('pi_evidence_policy_duplicate')
            policies[key] = pin
        self._policies, self._deployments = MappingProxyType(policies), deployments

    def digest_for_task(self, task):
        command = pi_task_command(task)
        pin = self._policies.get((task.tenant_id, task.project_id, task.assigned_agent_url))
        if pin is None or pin.preauthorized_read_only is not True:
            raise ValueError('pi_evidence_policy_not_preauthorized')
        context = ProviderInvocationContext.from_value(command.provider_context)
        artifact = pi_answer_artifact(command)
        if (command.policy_version != pin.policy_version
                or (context.provider_profile_id, context.selected_provider_id, context.selected_model_id)
                != (pin.provider_profile_id, pin.provider_id, pin.model_id)
                or command.node.side_effect_class not in {'none', 'read'}
                or (artifact is not None and not pin.preauthorized_result_artifacts)
                or command.node.input_artifacts or command.artifact_refs
                or 'coding.agent.pi' not in command.node.required_capabilities):
            raise ValueError('pi_evidence_policy_task_denied')
        expected = pin.manifest
        observed = self._deployments.require_manifest(tenant_id=task.tenant_id, project_id=task.project_id,
            worker_id=expected.worker_id, worker_url=task.assigned_agent_url)
        if observed != expected:
            raise ValueError('pi_evidence_policy_deployment_mismatch')
        return pin.digest
