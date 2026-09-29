from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services.organization_research_delegation_policy_service import (
    HubResearchDestinationCatalogAdapter,
    OrganizationResearchDelegationPolicyError,
    OrganizationResearchDelegationPolicyService,
    build_authoritative_research_context_policy,
    category_research_destination_policy,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    ProviderLocation,
)


class _Bundles:
    def __init__(self, bundle):
        self.bundle = bundle

    def get_by_id(self, bundle_id):
        return self.bundle if bundle_id == self.bundle.id else None


class _Destinations:
    def __init__(self, *, provider_location="local_container"):
        self.provider_location = provider_location
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "destination_id": "dst-current",
            "destination_digest": "d" * 64,
            "worker_url": kwargs["worker_url"],
            "worker_id": "worker-alpha",
            "worker_kind": "worker",
            "runtime_target_id": "runtime-target-alpha",
            "runtime_id": "research-runtime",
            "runtime_kind": "docker_container",
            "provider_id": "codex",
            "model_id": "research-model",
            "model_class": "code",
            "provider_location": self.provider_location,
            "data_residency": "local",
        }


class _Assignments:
    def __init__(self, worker_url="http://worker-alpha:5000"):
        self.worker_url = worker_url
        self.verify_calls = []

    def resolve_worker(self, _task):
        return self.worker_url

    def verify_dispatch(self, **kwargs):
        self.verify_calls.append(kwargs)


def _bundle():
    return SimpleNamespace(
        id="catalog-context-1",
        retrieval_run_id="catalog-retrieval-1",
        task_id="research-task-1",
        bundle_type="worker_execution_context",
        context_text="[SRC_0001] docs/paper.md\nauthoritative evidence",
        chunks=[
            {
                "engine": "organization_source_catalog",
                "source": "docs/paper.md",
                "content": "authoritative evidence",
                "score": 1.0,
                "metadata": {
                    "source_id": "SRC_0001",
                    "source_id_verified": True,
                },
            }
        ],
        token_estimate=20,
        bundle_metadata={
            "schema": "organization_source_catalog_context.v1",
            "authority": "hub",
            "llm_scope": "local_only",
            "catalog_task_id": "catalog-task-1",
            "catalog_id": "catalog-1",
            "catalog_hash": "a" * 64,
        },
    )


def _task(bundle):
    source_policy = build_authoritative_research_context_policy(
        bundle=bundle,
        catalog_task_id="catalog-task-1",
        source_catalog_id="catalog-1",
        source_catalog_hash="a" * 64,
    )
    return {
        "id": "research-task-1",
        "task_kind": "planning_research",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "organization_id": "org-1",
        "context_bundle_id": bundle.id,
        "required_capabilities": [
            "planning",
            "research",
            "source_analysis",
        ],
        "worker_execution_context": {
            "context_bundle_id": bundle.id,
            "llm_scope": "local_only",
            "source_context_policy": source_policy,
            "planning_research_binding": {
                "schema": "organization_category_research_binding.v1",
                "catalog_task_id": "catalog-task-1",
                "source_catalog_id": "catalog-1",
                "source_catalog_hash": "a" * 64,
                "allowed_source_refs": ["SRC_0001"],
                "allowed_run_refs": [],
                "context_bundle_id": bundle.id,
                "context_bundle_digest": source_policy[
                    "context_bundle_digest"
                ],
                "llm_scope": "local_only",
                "destination_policy": (
                    category_research_destination_policy()
                ),
            },
        },
    }


def test_resolve_context_reuses_exact_authoritative_bundle():
    bundle = _bundle()
    service = OrganizationResearchDelegationPolicyService(
        context_bundles=_Bundles(bundle),
        destinations=_Destinations(),
    )

    resolved = service.resolve_context(_task(bundle))

    assert resolved is not None
    assert resolved.bundle is bundle
    assert resolved.context_policy["context_bundle_id"] == bundle.id
    assert resolved.context_policy["llm_scope"] == "local_only"


def test_resolve_context_rejects_bundle_content_changed_after_binding():
    bundle = _bundle()
    task = _task(bundle)
    bundle.context_text = "tampered"
    service = OrganizationResearchDelegationPolicyService(
        context_bundles=_Bundles(bundle),
        destinations=_Destinations(),
    )

    with pytest.raises(
        OrganizationResearchDelegationPolicyError,
        match="category_research_context_bundle_digest_mismatch",
    ):
        service.resolve_context(task)


def test_resolve_context_requires_explicit_source_analysis_capability():
    bundle = _bundle()
    task = _task(bundle)
    task["required_capabilities"] = ["planning", "research"]
    service = OrganizationResearchDelegationPolicyService(
        context_bundles=_Bundles(bundle),
        destinations=_Destinations(),
    )

    with pytest.raises(
        OrganizationResearchDelegationPolicyError,
        match="category_research_required_capabilities_invalid",
    ):
        service.resolve_context(task)


def test_destination_binding_is_exact_and_rechecked_before_forward():
    bundle = _bundle()
    task = _task(bundle)
    destinations = _Destinations()
    service = OrganizationResearchDelegationPolicyService(
        context_bundles=_Bundles(bundle),
        destinations=destinations,
        assignment_bindings=_Assignments(),
    )
    binding = service.resolve_destination_binding(
        task=task,
        worker_url="http://worker-alpha:5000",
        selected_runtime_target_id="runtime-target-alpha",
        selected_runtime_kind="docker_container",
        preferred_provider="codex",
    )

    assert binding is not None
    assert binding["llm_scope"] == "local_only"
    assert binding["provider_id"] == "codex"
    assert binding["runtime_target_id"] == "runtime-target-alpha"
    assert len(binding["binding_digest"]) == 64

    service.verify_forward(
        task=task,
        worker_url="http://worker-alpha:5000",
        selected_runtime_target_id="runtime-target-alpha",
        selected_runtime_kind="docker_container",
        preferred_provider="codex",
        expected_context_bundle_id=bundle.id,
        expected_destination_binding=binding,
        expected_worker_job_id="job-1",
        expected_subtask_id="sub-1",
    )
    assert len(destinations.calls) == 2


def test_destination_binding_rejects_non_local_provider_for_local_only_context():
    bundle = _bundle()
    service = OrganizationResearchDelegationPolicyService(
        context_bundles=_Bundles(bundle),
        destinations=_Destinations(provider_location="external_region"),
        assignment_bindings=_Assignments(),
    )

    with pytest.raises(
        OrganizationResearchDelegationPolicyError,
        match="category_research_destination_llm_scope_denied",
    ):
        service.resolve_destination_binding(
            task=_task(bundle),
            worker_url="http://worker-alpha:5000",
            selected_runtime_target_id=None,
            selected_runtime_kind=None,
            preferred_provider=None,
        )


def test_hub_destination_adapter_resolves_current_worker_runtime_and_provider(
    monkeypatch,
):
    from agent.repository import agent_repo

    worker = SimpleNamespace(
        url="http://worker-alpha:5000",
        name="worker-alpha",
        role="worker",
        status="online",
        registration_validated=True,
        validation_errors=[],
        runtime_targets=[
            {
                "runtime_target_id": "runtime-target-alpha",
                "runtime_id": "research-runtime",
                "runtime_kind": "docker_container",
                "provider_id": "codex",
                "model_ids": ["research-model"],
                "provider_location": "local_container",
                "data_residency": "local",
                "source_access_authorized": True,
                "enabled": True,
            }
        ],
    )
    descriptor = DestinationDescriptor.create(
        worker_id="worker-alpha",
        worker_kind="worker",
        runtime_id="research-runtime",
        runtime_kind="docker_container",
        provider_id="codex",
        model_id="research-model",
        model_class="code",
        provider_location=ProviderLocation.LOCAL_CONTAINER,
        data_residency="local",
    )

    class Catalog:
        def list(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-1"
            assert kwargs["project_id"] == "project-1"
            assert kwargs["filters"] == {"provider_id": "codex"}
            return (descriptor,), None

    monkeypatch.setattr(
        agent_repo,
        "get_by_url",
        lambda worker_url: worker if worker_url == worker.url else None,
    )
    app = Flask(__name__)
    app.extensions["source_control_destination_catalog"] = Catalog()

    with app.app_context():
        destination = HubResearchDestinationCatalogAdapter().resolve(
            tenant_id="tenant-1",
            project_id="project-1",
            worker_url=worker.url,
            selected_runtime_target_id="runtime-target-alpha",
            selected_runtime_kind="docker_container",
            preferred_provider="codex",
        )

    assert destination["destination_id"] == descriptor.destination_id
    assert destination["worker_url"] == worker.url
    assert destination["runtime_target_id"] == "runtime-target-alpha"
    assert destination["provider_id"] == "codex"
    assert len(destination["destination_digest"]) == 64
