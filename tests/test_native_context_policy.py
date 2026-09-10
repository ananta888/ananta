"""Existing Hub CAP decisions projected onto exact Native Pi destinations."""

import copy
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.services import context_access_policy_service as domain_module
from agent.services.context_policy_lifecycle import ContextPolicyVersion, derive_context_policy_digest
from agent.services.native_context_blocks import NativeContextBlockProjector
from agent.services.native_context_policy_service import NativeContextPolicyService
from agent.services.source_classification_service import SourceClassificationService
from ananta_contracts.context_access_policy import ContextBlockAccessDecision, Decision
from ananta_contracts.source_control import DestinationDescriptor, ProviderLocation
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command


def build_policy_setup(monkeypatch):
    monkeypatch.setattr(domain_module, "get_context_access_policy_repo", Mock())
    monkeypatch.setattr(domain_module, "get_source_classification_service", SourceClassificationService)
    _, context, _ = composition()
    command = task_command(context)
    task = {"tenant_id": command.tenant_id, "project_id": "project-1"}
    document = {
        "schema": "ananta.context-access-policy.v1", "policy_id": "policy-1", "scope": "project",
        "defaults": {"send_allowed": False},
        "rules": [{
            "id": "public-code", "description": "Explicit public CodeCompass context",
            "source_types": ["codecompass_code"], "sensitivity": "public", "send_allowed": True,
            "cloud_allowed": False, "external_worker_allowed": False,
        }],
    }
    snapshot = ContextPolicyVersion(
        "policy-1", 1, task["tenant_id"], task["project_id"], "active", document,
        derive_context_policy_digest(document), "a" * 64, "synthetic-hub", "2026-01-01T00:00:00Z",
    )
    destination = DestinationDescriptor.create(
        worker_id="worker-1", worker_kind="native", runtime_id="native-pi", runtime_kind="docker",
        provider_id="ollama", model_id="selected-model", model_class="general",
        provider_location=ProviderLocation.LOCAL_CONTAINER, data_residency="local",
    )
    bundle = {
        "context_text": "unapproved assembled text must not be loaded",
        "chunks": [{
            "source_ref": "docs/public/example.py", "source_type": "codecompass_code",
            "sensitivity": "public", "content": "def example(): return 1",
        }],
        "bundle_metadata": {"native_context_access": {
            "policy_id": "policy-1", "destination_id": destination.destination_id,
            "provider_endpoint_identity": command.provider_binding.endpoint_identity,
        }},
    }
    policies, destinations = Mock(), Mock()
    policies.active.return_value, destinations.get.return_value = snapshot, destination
    domain = domain_module.ContextAccessPolicyService()
    service = NativeContextPolicyService(
        policies=policies, destinations=destinations, blocks=NativeContextBlockProjector(domain),
    )
    return service, task, bundle, command, policies, destinations, domain


@pytest.fixture
def policy_setup(monkeypatch):
    return build_policy_setup(monkeypatch)


def project(setup):
    service, task, bundle, command, *_ = setup
    return service.project(task=task, bundle=bundle, command=command, worker_id="worker-1")


def update_document(setup, mutate):
    policies = setup[4]
    snapshot = policies.active.return_value
    document = copy.deepcopy(snapshot.document)
    mutate(document)
    policies.active.return_value = replace(
        snapshot, document=document, policy_digest=derive_context_policy_digest(document),
    )


def test_active_project_policy_and_hub_destination_allow_only_selected_chunks(policy_setup):
    before = copy.deepcopy(policy_setup[2])
    value = project(policy_setup)
    assert json.loads(value.content) == [{"source_ref": "docs/public/example.py", "content": "def example(): return 1"}]
    assert policy_setup[2] == before and "unapproved assembled" not in value.content
    assert policy_setup[4].active.call_args.kwargs == {
        "tenant_id": "tenant-1", "project_id": "project-1", "policy_id": "policy-1",
    }
    assert policy_setup[5].get.call_args.kwargs["project_id"] == "project-1"


@pytest.mark.parametrize("mutation", ["missing", "revoked", "tenant", "project", "policy_id", "digest"])
def test_missing_revoked_or_mismatched_active_policy_blocks_context(policy_setup, mutation):
    policies = policy_setup[4]
    snapshot = policies.active.return_value
    if mutation == "missing":
        policies.active.return_value = None
    else:
        field = {"revoked": "state", "tenant": "tenant_id", "project": "project_id", "digest": "policy_digest"}.get(
            mutation, mutation,
        )
        value = "revoked" if mutation == "revoked" else "b" * 64 if mutation == "digest" else "foreign"
        policies.active.return_value = replace(snapshot, **{field: value})
    with pytest.raises(ValueError, match="native_context_active_policy_required"):
        project(policy_setup)
    policy_setup[5].get.assert_not_called()


@pytest.mark.parametrize("mutation", ["missing", "worker_id", "provider_id", "model_id", "endpoint", "reference"])
def test_foreign_or_changed_destination_never_becomes_a_worker_model_choice(policy_setup, mutation):
    destinations = policy_setup[5]
    if mutation == "missing":
        destinations.get.return_value = None
    elif mutation in {"endpoint", "reference"}:
        key = "provider_endpoint_identity" if mutation == "endpoint" else "destination_id"
        policy_setup[2]["bundle_metadata"]["native_context_access"][key] = "foreign"
    else:
        values = destinations.get.return_value.model_dump(exclude={"schema_version", "authority", "destination_id"})
        values[mutation] = "foreign"
        destination = DestinationDescriptor.create(**values)
        destinations.get.return_value = destination
        policy_setup[2]["bundle_metadata"]["native_context_access"]["destination_id"] = destination.destination_id
    with pytest.raises(ValueError, match="native_context_destination_binding_mismatch"):
        project(policy_setup)


@pytest.mark.parametrize("location", [ProviderLocation.PRIVATE_NETWORK, ProviderLocation.TENANT_REGION,
                                     ProviderLocation.EXTERNAL_REGION])
def test_nonlocal_source_control_locations_do_not_fall_back_to_local(policy_setup, location):
    destinations = policy_setup[5]
    values = destinations.get.return_value.model_dump(exclude={"schema_version", "authority", "destination_id"})
    values["provider_location"] = location
    destination = DestinationDescriptor.create(**values)
    destinations.get.return_value = destination
    policy_setup[2]["bundle_metadata"]["native_context_access"]["destination_id"] = destination.destination_id
    with pytest.raises(ValueError, match="native_context_policy_denied"):
        project(policy_setup)


def test_detected_secret_cannot_hide_behind_public_chunk_metadata(policy_setup):
    policy_setup[2]["chunks"][0]["content"] = "api_key=synthetic-secret-value"
    with pytest.raises(ValueError, match="native_context_send_grant_required"):
        project(policy_setup)


def test_explicit_secret_redaction_policy_never_emits_original_content(policy_setup):
    policy_setup[2]["chunks"][0]["content"] = "api_key=synthetic-secret-value"
    update_document(policy_setup, lambda doc: doc["rules"][0].update(sensitivity="secret", redaction_required=True))
    result = project(policy_setup)
    assert "synthetic-secret-value" not in result.content and "[REDACTED]" in result.content


@pytest.mark.parametrize("mutation", ["approval", "read_only", "truthy", "unknown_enum", "malformed_list"])
def test_pending_or_malformed_policy_never_implicitly_grants_send(policy_setup, mutation):
    def mutate(doc):
        rule = doc["rules"][0]
        if mutation == "approval":
            rule["approval_required"] = True
        elif mutation == "read_only":
            rule.pop("send_allowed")
            rule["read_allowed"] = True
        elif mutation == "truthy":
            rule["send_allowed"] = "false"
        elif mutation == "unknown_enum":
            rule["source_types"] = ["future-source"]
        else:
            rule["allowed_model_scopes"] = "local_model"
    update_document(policy_setup, mutate)
    with pytest.raises(
        ValueError, match="native_context_(approval_required|send_grant_required|policy_document_invalid)",
    ):
        project(policy_setup)


@pytest.mark.parametrize("mutation", ["missing", "oversized", "approval", "classification", "url", "control"])
def test_unstructured_oversized_or_untrusted_chunks_are_not_loaded(policy_setup, mutation):
    bundle = policy_setup[2]
    if mutation == "missing":
        bundle["chunks"] = []
    elif mutation == "oversized":
        bundle["chunks"][0]["content"] = "x" * 24_001
    elif mutation == "approval":
        bundle["chunks"][0]["approval_override_id"] = "invented"
    elif mutation == "classification":
        bundle["chunks"][0]["source_type"] = "future-source"
    elif mutation == "url":
        bundle["chunks"][0]["source_ref"] = "https://user:private@example.com"
    else:
        bundle["chunks"][0]["source_ref"] = "path\nprivate"
    with pytest.raises(ValueError, match="native_context_chunk"):
        project(policy_setup)


@pytest.mark.parametrize("decision", [None, "allow_redacted", Decision.approval_required, Decision.unavailable])
def test_non_grant_domain_receipt_never_passes_through_original_text(policy_setup, decision):
    policy_setup[6].get_decision = Mock(return_value=ContextBlockAccessDecision("b", "s", [], decision))
    with pytest.raises(ValueError, match="native_context_policy_denied"):
        project(policy_setup)


@pytest.mark.parametrize("engine,source_type", [
    ("codecompass_fts", "codecompass_code"), ("codecompass_graph", "codecompass_graph"),
    ("repository_map", "local_file"),
])
def test_actual_persisted_retrieval_chunk_shape_is_classified_and_projected(policy_setup, engine, source_type):
    policy_setup[2]["chunks"] = [{
        "engine": engine, "source": "docs/public/example.py", "content": "def example(): return 1",
        "score": 0.9, "metadata": {"record_id": "synthetic-record", "file": "docs/public/example.py"},
    }]
    update_document(policy_setup, lambda doc: doc["rules"][0].update(source_types=[source_type]))
    assert "def example(): return 1" in project(policy_setup).content


@pytest.mark.parametrize("mutation", ["text", "source", "kind", "approval", "metadata"])
def test_conflicting_or_unsafe_nested_retrieval_fields_are_rejected(policy_setup, mutation):
    chunk = policy_setup[2]["chunks"][0]
    if mutation == "text":
        chunk["text"] = "foreign content"
    elif mutation == "source":
        chunk["source"] = "foreign source"
    elif mutation == "kind":
        chunk["metadata"] = {"source_type": "env_file"}
    elif mutation == "approval":
        chunk["metadata"] = {"approval_scope": {"approved": True}}
    else:
        chunk["metadata"] = "foreign"
    with pytest.raises(ValueError, match="native_context_chunk"):
        project(policy_setup)
