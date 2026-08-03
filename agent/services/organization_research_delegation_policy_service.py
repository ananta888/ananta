"""Hub-owned source-context and destination guard for Category research."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from flask import current_app, has_app_context

from agent.services.organization_research_assignment_binding_service import (
    OrganizationResearchAssignmentBindingError,
    OrganizationResearchAssignmentBindingService,
)
from agent.services.source_destination_resolution import (
    source_destination_digest,
)

_CONTEXT_POLICY_SCHEMA = "organization_research_source_context_policy.v1"
_DESTINATION_POLICY_SCHEMA = "organization_research_destination_policy.v1"
_DESTINATION_BINDING_SCHEMA = "organization_research_destination_binding.v1"
_CATALOG_CONTEXT_SCHEMA = "organization_source_catalog_context.v1"
_RESEARCH_BINDING_SCHEMA = "organization_category_research_binding.v1"
_REQUIRED_RESEARCH_CAPABILITIES = frozenset(
    {"planning", "research", "source_analysis"}
)


class OrganizationResearchDelegationPolicyError(RuntimeError):
    """Stable fail-closed error for the governed research dispatch boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class ResearchContextBundlePort(Protocol):
    def get_by_id(self, bundle_id: str) -> Any | None: ...


class ResearchDestinationPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        worker_url: str,
        selected_runtime_target_id: str | None,
        selected_runtime_kind: str | None,
        preferred_provider: str | None,
    ) -> Mapping[str, Any]: ...


class ResearchAssignmentBindingPort(Protocol):
    def resolve_worker(self, task: Mapping[str, Any]) -> str | None: ...

    def verify_dispatch(
        self,
        *,
        task: Mapping[str, Any],
        worker_url: str,
        worker_job_id: str,
        subtask_id: str,
        context_bundle_id: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthoritativeResearchContext:
    bundle: Any
    context_policy: Mapping[str, Any]


class RepositoryResearchContextBundleAdapter:
    """Read the bundle through the existing Hub repository facade."""

    @staticmethod
    def get_by_id(bundle_id: str) -> Any | None:
        from agent.repository import context_bundle_repo

        return context_bundle_repo.get_by_id(bundle_id)


class HubResearchDestinationCatalogAdapter:
    """Resolve one current destination from Hub-owned worker/model evidence."""

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        worker_url: str,
        selected_runtime_target_id: str | None,
        selected_runtime_kind: str | None,
        preferred_provider: str | None,
    ) -> Mapping[str, Any]:
        from agent.repository import agent_repo

        worker = agent_repo.get_by_url(worker_url)
        if (
            worker is None
            or str(getattr(worker, "role", "") or "") != "worker"
            or str(getattr(worker, "status", "") or "") != "online"
            or not bool(getattr(worker, "registration_validated", False))
            or list(getattr(worker, "validation_errors", None) or [])
        ):
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_worker_invalid"
            )
        if not has_app_context():
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_catalog_unavailable"
            )
        catalog = current_app.extensions.get("source_control_destination_catalog")
        if catalog is None or not callable(getattr(catalog, "list", None)):
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_catalog_unavailable"
            )

        descriptors = self._scoped_descriptors(
            catalog,
            tenant_id=tenant_id,
            project_id=project_id,
            preferred_provider=preferred_provider,
        )
        candidates: list[dict[str, Any]] = []
        worker_id = str(getattr(worker, "name", "") or worker_url)
        for raw_target in list(getattr(worker, "runtime_targets", None) or []):
            if not isinstance(raw_target, Mapping):
                continue
            target = dict(raw_target)
            runtime_target_id = str(
                target.get("runtime_target_id") or ""
            ).strip()
            runtime_id = str(target.get("runtime_id") or "").strip()
            runtime_kind = str(target.get("runtime_kind") or "").strip()
            provider_id = str(target.get("provider_id") or "").strip()
            if (
                not runtime_target_id
                or not runtime_id
                or not runtime_kind
                or not provider_id
                or target.get("enabled", True) is not True
                or target.get("source_access_authorized") is not True
            ):
                continue
            if (
                selected_runtime_target_id
                and runtime_target_id != selected_runtime_target_id
            ):
                continue
            if selected_runtime_kind and runtime_kind != selected_runtime_kind:
                continue
            if preferred_provider and provider_id != preferred_provider:
                continue
            raw_model_ids = target.get("model_ids")
            model_ids = (
                list(raw_model_ids)
                if isinstance(raw_model_ids, list)
                else [target.get("model_id")]
            )
            for descriptor in descriptors:
                descriptor_data = self._descriptor_data(descriptor)
                if (
                    descriptor_data.get("worker_id") != worker_id
                    or descriptor_data.get("runtime_id") != runtime_id
                    or descriptor_data.get("runtime_kind") != runtime_kind
                    or descriptor_data.get("provider_id") != provider_id
                    or descriptor_data.get("model_id")
                    not in {str(value or "") for value in model_ids}
                    or descriptor_data.get("provider_location")
                    != str(target.get("provider_location") or "")
                    or descriptor_data.get("data_residency")
                    != str(target.get("data_residency") or "")
                ):
                    continue
                candidates.append(
                    {
                        **descriptor_data,
                        "worker_url": worker_url,
                        "runtime_target_id": runtime_target_id,
                        "destination_digest": source_destination_digest(
                            descriptor
                        ),
                    }
                )

        unique = {
            str(candidate.get("destination_id") or ""): candidate
            for candidate in candidates
            if str(candidate.get("destination_id") or "")
        }
        if not unique:
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_not_found"
            )
        if len(unique) != 1:
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_ambiguous"
            )
        return next(iter(unique.values()))

    @staticmethod
    def _descriptor_data(descriptor: Any) -> dict[str, Any]:
        if hasattr(descriptor, "model_dump"):
            payload = descriptor.model_dump(mode="json", by_alias=True)
        elif isinstance(descriptor, Mapping):
            payload = dict(descriptor)
        else:
            raise OrganizationResearchDelegationPolicyError(
                "category_research_destination_catalog_invalid"
            )
        return {
            key: str(payload.get(key) or "")
            for key in (
                "destination_id",
                "worker_id",
                "worker_kind",
                "runtime_id",
                "runtime_kind",
                "provider_id",
                "model_id",
                "model_class",
                "provider_location",
                "data_residency",
            )
        }

    @staticmethod
    def _scoped_descriptors(
        catalog: Any,
        *,
        tenant_id: str,
        project_id: str,
        preferred_provider: str | None,
    ) -> list[Any]:
        cursor: str | None = None
        descriptors: list[Any] = []
        filters = (
            {"provider_id": preferred_provider}
            if preferred_provider
            else {}
        )
        for _page in range(8):
            page, cursor = catalog.list(
                tenant_id=tenant_id,
                project_id=project_id,
                cursor=cursor,
                limit=200,
                filters=filters,
            )
            descriptors.extend(list(page or []))
            if cursor is None:
                return descriptors
        raise OrganizationResearchDelegationPolicyError(
            "category_research_destination_catalog_too_large"
        )


def category_research_destination_policy() -> dict[str, Any]:
    """Return the immutable minimum destination constraints for raw sources."""

    return {
        "schema": _DESTINATION_POLICY_SCHEMA,
        "llm_scope": "local_only",
        "allowed_provider_locations": ["local_container"],
        "require_current_hub_catalog": True,
        "require_registered_worker": True,
        "require_single_destination": True,
    }


def context_bundle_integrity_digest(bundle: Any) -> str:
    """Hash only stable ContextBundle fields used by Worker execution."""

    return _canonical_sha256(
        {
            "id": str(getattr(bundle, "id", "") or ""),
            "retrieval_run_id": str(
                getattr(bundle, "retrieval_run_id", "") or ""
            ),
            "task_id": str(getattr(bundle, "task_id", "") or ""),
            "bundle_type": str(
                getattr(bundle, "bundle_type", "") or ""
            ),
            "context_text": str(
                getattr(bundle, "context_text", "") or ""
            ),
            "chunks": list(getattr(bundle, "chunks", None) or []),
            "token_estimate": int(
                getattr(bundle, "token_estimate", 0) or 0
            ),
            "bundle_metadata": dict(
                getattr(bundle, "bundle_metadata", None) or {}
            ),
        }
    )


def build_authoritative_research_context_policy(
    *,
    bundle: Any,
    catalog_task_id: str,
    source_catalog_id: str,
    source_catalog_hash: str,
) -> dict[str, Any]:
    """Build the Hub-owned immutable bundle and destination policy binding."""

    return {
        "schema": _CONTEXT_POLICY_SCHEMA,
        "authority": "hub",
        "mode": "authoritative_source_catalog_bundle",
        "context_bundle_id": str(getattr(bundle, "id", "") or ""),
        "context_bundle_digest": context_bundle_integrity_digest(bundle),
        "catalog_task_id": str(catalog_task_id or ""),
        "source_catalog_id": str(source_catalog_id or ""),
        "source_catalog_hash": str(source_catalog_hash or ""),
        "llm_scope": "local_only",
        "destination_policy": category_research_destination_policy(),
    }


class OrganizationResearchDelegationPolicyService:
    """Protect exact Category-research context through Hub delegation."""

    def __init__(
        self,
        *,
        context_bundles: ResearchContextBundlePort | None = None,
        destinations: ResearchDestinationPort | None = None,
        assignment_bindings: ResearchAssignmentBindingPort | None = None,
    ) -> None:
        self._context_bundles = (
            context_bundles or RepositoryResearchContextBundleAdapter()
        )
        self._destinations = (
            destinations or HubResearchDestinationCatalogAdapter()
        )
        self._assignment_bindings = (
            assignment_bindings
            or OrganizationResearchAssignmentBindingService()
        )

    @staticmethod
    def governs(task: Mapping[str, Any]) -> bool:
        return bool(
            str(task.get("task_kind") or "").strip()
            == "planning_research"
            and str(task.get("organization_id") or "").strip()
        )

    def resolve_context(
        self,
        task: Mapping[str, Any],
    ) -> AuthoritativeResearchContext | None:
        if not self.governs(task):
            return None
        task_capabilities = {
            str(value).strip()
            for value in list(task.get("required_capabilities") or [])
            if str(value).strip()
        }
        if not _REQUIRED_RESEARCH_CAPABILITIES.issubset(
            task_capabilities
        ):
            self._fail("category_research_required_capabilities_invalid")
        worker_context = self._mapping(task.get("worker_execution_context"))
        binding = self._mapping(worker_context.get("planning_research_binding"))
        policy = self._mapping(worker_context.get("source_context_policy"))
        if binding.get("schema") != _RESEARCH_BINDING_SCHEMA:
            self._fail("category_research_delegation_binding_missing")
        if policy.get("schema") != _CONTEXT_POLICY_SCHEMA:
            self._fail("category_research_context_policy_missing")
        if policy.get("authority") != "hub":
            self._fail("category_research_context_authority_invalid")
        destination_policy = self._mapping(policy.get("destination_policy"))
        if (
            destination_policy != category_research_destination_policy()
            or self._mapping(binding.get("destination_policy"))
            != destination_policy
            or binding.get("llm_scope") != "local_only"
            or policy.get("llm_scope") != "local_only"
            or worker_context.get("llm_scope") != "local_only"
        ):
            self._fail("category_research_destination_policy_invalid")

        bundle_id = str(task.get("context_bundle_id") or "").strip()
        if (
            not bundle_id
            or str(worker_context.get("context_bundle_id") or "")
            != bundle_id
            or str(binding.get("context_bundle_id") or "") != bundle_id
            or str(policy.get("context_bundle_id") or "") != bundle_id
        ):
            self._fail("category_research_context_bundle_binding_invalid")
        bundle = self._context_bundles.get_by_id(bundle_id)
        if bundle is None:
            self._fail("category_research_context_bundle_not_found")
        metadata = dict(getattr(bundle, "bundle_metadata", None) or {})
        if (
            str(getattr(bundle, "task_id", "") or "")
            != str(task.get("id") or "")
            or str(getattr(bundle, "bundle_type", "") or "")
            != "worker_execution_context"
            or metadata.get("schema") != _CATALOG_CONTEXT_SCHEMA
            or metadata.get("llm_scope") != "local_only"
            or str(metadata.get("catalog_task_id") or "")
            != str(binding.get("catalog_task_id") or "")
            or str(metadata.get("catalog_id") or "")
            != str(binding.get("source_catalog_id") or "")
            or str(metadata.get("catalog_hash") or "")
            != str(binding.get("source_catalog_hash") or "")
            or str(policy.get("catalog_task_id") or "")
            != str(binding.get("catalog_task_id") or "")
            or str(policy.get("source_catalog_id") or "")
            != str(binding.get("source_catalog_id") or "")
            or str(policy.get("source_catalog_hash") or "")
            != str(binding.get("source_catalog_hash") or "")
        ):
            self._fail("category_research_context_bundle_authority_invalid")
        digest = context_bundle_integrity_digest(bundle)
        if (
            str(policy.get("context_bundle_digest") or "") != digest
            or str(binding.get("context_bundle_digest") or "") != digest
        ):
            self._fail("category_research_context_bundle_digest_mismatch")
        allowed_refs = {
            str(value)
            for value in list(binding.get("allowed_source_refs") or [])
            if str(value)
        }
        chunk_refs = {
            str(self._mapping(chunk.get("metadata")).get("source_id") or "")
            for chunk in list(getattr(bundle, "chunks", None) or [])
            if isinstance(chunk, Mapping)
        }
        if not allowed_refs or chunk_refs != allowed_refs:
            self._fail("category_research_context_source_scope_mismatch")
        return AuthoritativeResearchContext(
            bundle=bundle,
            context_policy=dict(policy),
        )

    def resolve_destination_binding(
        self,
        *,
        task: Mapping[str, Any],
        worker_url: str,
        selected_runtime_target_id: str | None,
        selected_runtime_kind: str | None,
        preferred_provider: str | None,
    ) -> dict[str, Any] | None:
        context = self.resolve_context(task)
        if context is None:
            return None
        bound_worker = self._assignment_bindings.resolve_worker(task)
        if not bound_worker:
            self._fail("category_research_assignment_binding_invalid")
        if str(bound_worker) != str(worker_url or ""):
            self._fail("category_research_assignment_worker_changed")
        destination = dict(
            self._destinations.resolve(
                tenant_id=str(task.get("tenant_id") or ""),
                project_id=str(task.get("project_id") or ""),
                worker_url=str(worker_url or ""),
                selected_runtime_target_id=(
                    str(selected_runtime_target_id or "").strip() or None
                ),
                selected_runtime_kind=(
                    str(selected_runtime_kind or "").strip() or None
                ),
                preferred_provider=(
                    str(preferred_provider or "").strip() or None
                ),
            )
        )
        required = (
            "destination_id",
            "destination_digest",
            "worker_url",
            "worker_id",
            "worker_kind",
            "runtime_target_id",
            "runtime_id",
            "runtime_kind",
            "provider_id",
            "model_id",
            "model_class",
            "provider_location",
            "data_residency",
        )
        if any(not str(destination.get(key) or "").strip() for key in required):
            self._fail("category_research_destination_binding_incomplete")
        if str(destination.get("worker_url") or "") != str(worker_url or ""):
            self._fail("category_research_destination_worker_changed")
        policy = category_research_destination_policy()
        provider_location = str(destination.get("provider_location") or "")
        llm_scope = self._llm_scope(provider_location)
        if (
            provider_location
            not in set(policy["allowed_provider_locations"])
            or llm_scope != str(policy["llm_scope"])
        ):
            self._fail("category_research_destination_llm_scope_denied")
        binding = {
            "schema": _DESTINATION_BINDING_SCHEMA,
            **{key: str(destination[key]) for key in required},
            "llm_scope": llm_scope,
        }
        binding["binding_digest"] = _canonical_sha256(binding)
        return binding

    def verify_forward(
        self,
        *,
        task: Mapping[str, Any],
        worker_url: str,
        selected_runtime_target_id: str | None,
        selected_runtime_kind: str | None,
        preferred_provider: str | None,
        expected_context_bundle_id: str,
        expected_destination_binding: Mapping[str, Any] | None,
        expected_worker_job_id: str = "",
        expected_subtask_id: str = "",
    ) -> None:
        context = self.resolve_context(task)
        if context is None:
            return
        if str(getattr(context.bundle, "id", "") or "") != str(
            expected_context_bundle_id or ""
        ):
            self._fail("category_research_context_bundle_changed")
        try:
            self._assignment_bindings.verify_dispatch(
                task=task,
                worker_url=worker_url,
                worker_job_id=expected_worker_job_id,
                subtask_id=expected_subtask_id,
                context_bundle_id=expected_context_bundle_id,
            )
        except OrganizationResearchAssignmentBindingError as exc:
            self._fail(exc.reason_code)
        expected = dict(expected_destination_binding or {})
        if expected.get("schema") != _DESTINATION_BINDING_SCHEMA:
            self._fail("category_research_destination_binding_missing")
        current = self.resolve_destination_binding(
            task=task,
            worker_url=worker_url,
            selected_runtime_target_id=selected_runtime_target_id,
            selected_runtime_kind=selected_runtime_kind,
            preferred_provider=preferred_provider,
        )
        if current != expected:
            self._fail("category_research_destination_changed_before_forward")

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _llm_scope(provider_location: str) -> str:
        return {
            "local_container": "local_only",
            "private_network": "trusted_private_cloud",
            "tenant_region": "trusted_private_cloud",
            "external_region": "external_cloud_allowed",
        }.get(str(provider_location or ""), "unknown")

    @staticmethod
    def _fail(reason_code: str) -> None:
        raise OrganizationResearchDelegationPolicyError(reason_code)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


_SERVICE: OrganizationResearchDelegationPolicyService | None = None


def get_organization_research_delegation_policy_service(
) -> OrganizationResearchDelegationPolicyService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OrganizationResearchDelegationPolicyService()
    return _SERVICE


__all__ = [
    "AuthoritativeResearchContext",
    "HubResearchDestinationCatalogAdapter",
    "OrganizationResearchDelegationPolicyError",
    "OrganizationResearchDelegationPolicyService",
    "ResearchContextBundlePort",
    "ResearchDestinationPort",
    "ResearchAssignmentBindingPort",
    "build_authoritative_research_context_policy",
    "category_research_destination_policy",
    "context_bundle_integrity_digest",
    "get_organization_research_delegation_policy_service",
]
