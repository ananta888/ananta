from dataclasses import FrozenInstanceError, replace

import pytest

from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryContractError,
    MemoryNamespace,
    MemoryOperation,
    MemoryScope,
    MemorySecurityMetadata,
    normalize_legacy_memory_metadata,
)
from tests.security.memory_fixtures import NAMESPACE, grant, policy


def test_legacy_classification_is_conservative_idempotent_and_preserves_provenance():
    original = {"memory_scope": "global", "approved": True, "source_refs": ["tool-output"], "sensitivity": "secret"}
    result = normalize_legacy_memory_metadata(original)
    assert result["memory_security"] == {
        "scope": "task", "sensitivity": "secret", "retention_class": "task", "trust_level": "untrusted",
    }
    assert result["source_refs"] == original["source_refs"]
    assert "memory_security" not in original
    assert normalize_legacy_memory_metadata(result) == result


def test_missing_metadata_never_means_global_or_trusted():
    assert normalize_legacy_memory_metadata(None)["memory_security"] == MemorySecurityMetadata().to_mapping()


@pytest.mark.parametrize("value", [[], "global", {"scope": "unknown"}, {"trust_level": "system"},
                                   {"scope": None}, {"sensitivity": "unknown"}, {"extra_authority": True}])
def test_invalid_security_metadata_is_rejected(value):
    with pytest.raises(MemoryContractError):
        MemorySecurityMetadata.from_mapping(value)


@pytest.mark.parametrize("field", ["tenant_id", "workspace_id", "task_id", "run_id", "goal_id", "conversation_id"])
@pytest.mark.parametrize("value", ["", "*", " bad ", "bad\nnamespace"])
def test_namespace_identifiers_are_strict(field, value):
    with pytest.raises(MemoryContractError):
        replace(NAMESPACE, **{field: value})


@pytest.mark.parametrize("scope", [MemoryScope.run, MemoryScope.task, MemoryScope.goal,
                                   MemoryScope.conversation, MemoryScope.workspace])
def test_missing_scope_binding_does_not_broaden_access(scope):
    with pytest.raises(MemoryContractError, match="namespace_incomplete"):
        MemoryNamespace("tenant").key(scope)


def test_scope_kind_is_part_of_namespace_key_even_if_local_identifiers_match():
    namespace = replace(NAMESPACE, goal_id="same-local-id", conversation_id="same-local-id")
    assert namespace.key(MemoryScope.goal) != namespace.key(MemoryScope.conversation)


@pytest.mark.parametrize("operations", [[], (), ("read",), (MemoryOperation.read, MemoryOperation.read)])
def test_capability_operations_are_immutable_typed_and_unique(operations):
    with pytest.raises(MemoryContractError):
        grant(operations=operations)


@pytest.mark.parametrize("expiry", [True, -1, 0, float("nan"), float("inf")])
def test_invalid_capability_expiry_is_rejected(expiry):
    with pytest.raises(MemoryContractError):
        grant(expires_at=expiry)


def test_manifest_cannot_be_mutated_and_digest_covers_grants():
    initial = policy()
    with pytest.raises(FrozenInstanceError):
        initial.capabilities = ()
    with pytest.raises(FrozenInstanceError):
        initial.capabilities[0].operations = (MemoryOperation.promote,)
    assert initial.digest != replace(initial, capabilities=()).digest


def test_capabilities_cannot_outlive_run_or_grant_another_tenant():
    for capability in (grant(expires_at=1001), grant(namespace=replace(NAMESPACE, tenant_id="foreign"))):
        with pytest.raises(MemoryContractError):
            policy((capability,))


def test_actor_and_run_tenant_must_match():
    with pytest.raises(MemoryContractError):
        policy(actor=MemoryActor("foreign", "test-actor"))
