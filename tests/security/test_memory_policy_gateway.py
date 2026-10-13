from dataclasses import replace

import pytest

from agent.services.memory_policy_gateway import MemoryAccessError
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryOperation,
    MemoryScope,
    MemorySecurityMetadata,
    MemorySensitivity,
    MemoryTarget,
)
from tests.security.memory_fixtures import ACTOR, NAMESPACE, gateway, grant, policy


def target(scope=MemoryScope.run, **changes):
    return MemoryTarget("run-scratchpad", replace(NAMESPACE, **changes), MemorySecurityMetadata(scope=scope))


@pytest.mark.parametrize("operation", tuple(MemoryOperation))
def test_each_operation_requires_an_explicit_capability(operation):
    service, _policies, audit = gateway(policy(()))
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.authorize(actor=ACTOR, run_id=NAMESPACE.run_id, operation=operation, target=target())
    assert len(audit.decisions) == 1 and not audit.decisions[0].allowed


@pytest.mark.parametrize("operation", tuple(MemoryOperation))
def test_explicit_current_capability_allows_only_selected_operation(operation):
    service, _policies, audit = gateway(policy((grant(operations=(operation,)),)))
    decision = service.authorize(actor=ACTOR, run_id=NAMESPACE.run_id, operation=operation, target=target())
    assert decision.allowed and decision.policy_digest == policy((grant(operations=(operation,)),)).digest
    assert audit.decisions == [decision]
    other = next(item for item in MemoryOperation if item != operation)
    with pytest.raises(MemoryAccessError):
        service.authorize(actor=ACTOR, run_id=NAMESPACE.run_id, operation=other, target=target())


@pytest.mark.parametrize("field", ["tenant_id", "workspace_id", "task_id", "run_id", "goal_id"])
def test_manipulated_target_binding_is_denied(field):
    service, _policies, audit = gateway()
    with pytest.raises(MemoryAccessError, match="namespace_mismatch"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=target(**{field: "foreign"}))
    assert not audit.decisions[-1].allowed


@pytest.mark.parametrize("scope,field", [
    (MemoryScope.run, "run_id"), (MemoryScope.task, "task_id"), (MemoryScope.goal, "goal_id"),
    (MemoryScope.conversation, "conversation_id"), (MemoryScope.workspace, "workspace_id"),
    (MemoryScope.global_, "tenant_id"),
])
def test_every_scope_has_a_positive_control_and_a_foreign_namespace_control(scope, field):
    namespace = replace(NAMESPACE, conversation_id="synthetic-conversation")
    capability = replace(grant(), namespace=namespace, scope=scope, channel_id="hub-result-memory")
    service, _policies, _audit = gateway(policy((capability,), namespace=namespace))
    resource = MemoryTarget("hub-result-memory", namespace, MemorySecurityMetadata(scope=scope))
    assert service.authorize_read(actor=ACTOR, run_id=namespace.run_id, target=resource).allowed
    foreign = replace(resource, namespace=replace(namespace, **{field: "foreign"}))
    with pytest.raises(MemoryAccessError, match="namespace_mismatch"):
        service.authorize_read(actor=ACTOR, run_id=namespace.run_id, target=foreign)


@pytest.mark.parametrize("actor", [MemoryActor("foreign", ACTOR.subject), MemoryActor(ACTOR.tenant_id, "foreign")])
def test_actor_cannot_reuse_another_run_policy(actor):
    service, _policies, _audit = gateway()
    with pytest.raises(MemoryAccessError, match="run_binding_mismatch"):
        service.authorize_read(actor=actor, run_id=NAMESPACE.run_id, target=target())


@pytest.mark.parametrize("value", [policy(active=False), policy(expires_at=900), policy((grant(expires_at=100),))])
def test_terminal_expired_or_revoked_run_is_denied(value):
    clock = (lambda: 900) if value.expires_at == 900 else (lambda: 100)
    service, _policies, _audit = gateway(value, clock=clock)
    with pytest.raises(MemoryAccessError, match="inactive|capability_missing"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=target())


def test_policy_failure_is_audited_and_never_exposes_backend_information():
    service, policies, audit = gateway()
    policies.error = True
    with pytest.raises(MemoryAccessError, match="^memory_policy_unavailable$"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=target())
    assert not audit.decisions[-1].allowed and audit.decisions[-1].target == target()


def test_audit_failure_prevents_access_even_with_valid_grant():
    service, _policies, audit = gateway()
    audit.error = True
    with pytest.raises(MemoryAccessError, match="^memory_audit_unavailable$"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=target())


def test_registered_channel_is_required_independently_of_capability():
    service, _policies, _audit = gateway(policy((grant(channel="unregistered-channel"),)))
    unknown = replace(target(), channel_id="unregistered-channel")
    with pytest.raises(MemoryAccessError, match="channel_unregistered"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=unknown)


def test_sensitivity_cannot_be_lowered_by_requesting_a_different_operation():
    service, _policies, _audit = gateway()
    secret = replace(target(), security=MemorySecurityMetadata(
        scope=MemoryScope.run, sensitivity=MemorySensitivity.secret,
    ))
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=secret)


def test_global_access_is_never_inferred_from_task_grant():
    service, _policies, _audit = gateway()
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=target(MemoryScope.global_))


def test_promotion_requires_one_step_and_independent_destination_grant():
    source = target(MemoryScope.task)
    destination = target(MemoryScope.goal)
    service, policies, _audit = gateway(policy((grant(scope=MemoryScope.task, operations=(MemoryOperation.promote,)),)))
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.authorize_promote(actor=ACTOR, run_id=NAMESPACE.run_id, target=source, destination=destination)
    policies.value = policy((grant(scope=MemoryScope.task, operations=(MemoryOperation.promote,)),
                             grant(scope=MemoryScope.goal, operations=(MemoryOperation.append,))))
    decisions = service.authorize_promote(actor=ACTOR, run_id=NAMESPACE.run_id, target=source, destination=destination)
    assert all(item.allowed for item in decisions)
    with pytest.raises(MemoryAccessError, match="transition_invalid"):
        service.authorize_promote(actor=ACTOR, run_id=NAMESPACE.run_id, target=source,
                                  destination=target(MemoryScope.global_))


def test_promotion_rejects_changed_policy_even_if_revision_string_is_reused():
    capabilities = (grant(scope=MemoryScope.task, operations=(MemoryOperation.promote,)),
                    grant(scope=MemoryScope.goal, operations=(MemoryOperation.append,)))
    service, policies, audit = gateway(policy(capabilities))
    original = policies.current

    def changed(**request):
        if policies.calls:
            policies.value = replace(policies.value, expires_at=1100)
        return original(**request)

    policies.current = changed
    with pytest.raises(MemoryAccessError, match="policy_changed"):
        service.authorize_promote(actor=ACTOR, run_id=NAMESPACE.run_id,
                                  target=target(MemoryScope.task), destination=target(MemoryScope.goal))
    assert not audit.decisions[-1].allowed and audit.decisions[-1].reason_code == "memory_policy_changed"
