"""Hub admission of the exact approved Pi context bytes before RUN reservation."""

from typing import Protocol

from ananta_contracts.native_context_bundle import (
    NativeContextBundleProjection,
    native_context_digest,
    pi_context_bundle_reference,
)


class PiApprovedContextPort(Protocol):
    def read(self, *, session, task, command, worker_id) -> NativeContextBundleProjection:
        """Project current Hub-approved bytes in the caller-owned transaction."""
        ...


def pi_context_reference(task):
    return pi_context_bundle_reference(task.model_dump())


class PiContextEvidenceAdmission:
    def __init__(self, *, registry, contexts: PiApprovedContextPort | None):
        self._registry, self._contexts = registry, contexts

    def admit(self, *, session, task, command, worker_id, manifest, policy_digest):
        projection = self._projection(session=session, task=task, command=command, worker_id=worker_id)
        if projection is None:
            return None
        return self._registry.register_source(tenant_id=task.tenant_id, project_id=task.project_id,
            origin_type='pi_native_approved_context', origin_digest=self._origin(task, projection),
            content_digest=projection.content_digest, policy_digest=self._policy(policy_digest, projection),
            evidence_scope=manifest.evidence_scope, synthetic=manifest.synthetic)

    @staticmethod
    def _origin(task, projection):
        return native_context_digest({'hub_task_id': task.id, 'bundle_id': projection.bundle_id,
                                      'command_digest': projection.command_digest})

    @staticmethod
    def _policy(policy_digest, projection):
        return native_context_digest({'pi_policy_digest': policy_digest,
                                      'context_policy_digest': projection.policy_digest})

    def require_reserved(self, *, session, task, command, worker_id, assignment, policy_digest=None):
        sources = self._registry.assignment_sources(tenant_id=task.tenant_id, project_id=task.project_id,
                                                    assignment=assignment)
        reserved = [source for source in sources if source.origin_type == 'pi_native_approved_context']
        projection = self._projection(session=session, task=task, command=command, worker_id=worker_id)
        if projection is None:
            if reserved:
                raise ValueError('pi_context_evidence_reserved_input_missing')
            return None
        if policy_digest is None:
            inputs = [source for source in sources if source.origin_type == 'pi_native_task_input']
            if len(inputs) != 1:
                raise ValueError('pi_context_evidence_task_input_required')
            policy_digest = inputs[0].policy_digest
        if (len(reserved) != 1 or reserved[0].origin_digest != self._origin(task, projection)
                or reserved[0].content_digest != projection.content_digest
                or reserved[0].policy_digest != self._policy(policy_digest, projection)):
            raise ValueError('pi_context_evidence_reserved_input_mismatch')
        return projection

    def _projection(self, *, session, task, command, worker_id):
        reference = pi_context_reference(task)
        if reference is None:
            if not {'context_bundle_mode', 'context_policy_id', 'context_destination_id'}.isdisjoint(
                    command.node.metadata):
                raise ValueError('pi_context_evidence_reference_required')
            return None
        if self._contexts is None:
            raise ValueError('pi_context_evidence_projection_required')
        if not session.in_transaction():
            raise ValueError('pi_context_evidence_transaction_required')
        projection = self._contexts.read(session=session, task=task, command=command, worker_id=worker_id)
        if not isinstance(projection, NativeContextBundleProjection):
            raise ValueError('pi_context_evidence_projection_invalid')
        projection.assert_valid()
        if (projection.hub_task_id != task.id or projection.bundle_id != reference
                or projection.command_digest != native_context_digest(command.to_dict())):
            raise ValueError('pi_context_evidence_projection_binding_denied')
        return projection
