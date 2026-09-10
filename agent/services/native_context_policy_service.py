"""Project policy-approved context for an existing Hub-selected Pi destination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from agent.services.context_policy_lifecycle import ContextPolicyVersion, derive_context_policy_digest
from agent.services.native_context_blocks import NativeContextBlockProjector
from agent.services.source_destination_resolution import source_destination_digest
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from ananta_contracts.context_access_policy import DestinationContext, ModelScope, RequestedOperation
from ananta_contracts.native_context_bundle import NativeApprovedContext, native_context_digest
from ananta_contracts.source_control import DestinationDescriptor, ProviderLocation


class NativeActiveContextPolicyPort(Protocol):
    def active(self, *, tenant_id: str, project_id: str, policy_id: str) -> ContextPolicyVersion | None: ...


class NativeContextDestinationPort(Protocol):
    def get(self, *, tenant_id: str, project_id: str, destination_id: str) -> DestinationDescriptor | None: ...


class NativeContextPolicyService:
    """No default grants: resolve existing active policy and catalog entries."""

    def __init__(
        self, *, policies: NativeActiveContextPolicyPort, destinations: NativeContextDestinationPort,
        blocks: NativeContextBlockProjector,
    ) -> None:
        self._policies, self._destinations, self._blocks = policies, destinations, blocks

    def project(
        self, *, task: Mapping[str, Any], bundle: Any, command: NativeNodeCommand, worker_id: str,
    ) -> NativeApprovedContext:
        metadata = _field(bundle, "bundle_metadata")
        raw = metadata.get("native_context_access") if isinstance(metadata, Mapping) else None
        if not isinstance(raw, Mapping) or set(raw) != {"policy_id", "destination_id", "provider_endpoint_identity"}:
            raise ValueError("native_context_access_binding_required")
        if any(not isinstance(v, str) or not v or len(v) > 1024 for v in raw.values()):
            raise ValueError("native_context_access_binding_invalid")
        scope = {"tenant_id": task["tenant_id"], "project_id": task["project_id"]}
        policy = self._policies.active(**scope, policy_id=raw["policy_id"])
        self._assert_active_policy(policy, scope=scope, policy_id=raw["policy_id"])
        destination = self._destinations.get(**scope, destination_id=raw["destination_id"])
        binding = command.provider_binding
        if (
            not isinstance(destination, DestinationDescriptor) or destination.destination_id != raw["destination_id"]
            or destination.worker_id != worker_id or binding is None
            or destination.provider_id != binding.provider_id or destination.model_id != binding.model_id
            or raw["provider_endpoint_identity"] != binding.endpoint_identity
        ):
            raise ValueError("native_context_destination_binding_mismatch")
        content = self._blocks.project(
            chunks=_field(bundle, "chunks"), policy_document=policy.document,
            policy_version=policy.version, destination=_destination_context(destination),
        )
        # Bind the evaluated policy and catalog coordinates, not just a name.
        digest = native_context_digest({
            "policy_digest": policy.policy_digest, "policy_version": policy.version,
            "destination_digest": source_destination_digest(destination),
            "provider_endpoint_identity": binding.endpoint_identity,
        })
        approved = NativeApprovedContext(content, digest)
        approved.assert_valid()
        return approved

    @staticmethod
    def _assert_active_policy(policy: Any, *, scope: Mapping[str, str], policy_id: str) -> None:
        if (
            not isinstance(policy, ContextPolicyVersion) or policy.state != "active"
            or policy.tenant_id != scope["tenant_id"] or policy.project_id != scope["project_id"]
            or policy.policy_id != policy_id or policy.policy_digest != derive_context_policy_digest(policy.document)
            or policy.document.get("policy_id") != policy_id
        ):
            raise ValueError("native_context_active_policy_required")


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _destination_context(destination: DestinationDescriptor) -> DestinationContext:
    location = destination.provider_location
    local = location is ProviderLocation.LOCAL_CONTAINER
    private = location is ProviderLocation.PRIVATE_NETWORK
    # Source-control location enums are not the legacy CAP string vocabulary.
    # Do not let an unknown cloud location silently become "local".
    value = DestinationContext(
        worker_id=destination.worker_id, worker_kind=destination.worker_kind,
        runtime_target_id=destination.runtime_id, runtime_kind=destination.runtime_kind,
        provider_id=destination.provider_id, provider_location=location.value, model_id=destination.model_id,
        model_scope=(
            ModelScope.local_model if local else ModelScope.private_remote if private else ModelScope.public_cloud
        ),
        cloud_effective=not local and not private, external_effective=not local, local_effective=local,
        requested_operation=RequestedOperation.send_to_llm,
    )
    if any(part in destination.runtime_kind.lower() for part in ("remote", "external", "cloud")):
        value = replace(
            value, external_effective=True, local_effective=False, cloud_effective=True,
            model_scope=ModelScope.public_cloud,
        )
    return value
