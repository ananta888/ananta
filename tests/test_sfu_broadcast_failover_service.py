from dataclasses import replace

from agent.services.sfu_broadcast_failover_service import (
    InMemorySfuFailoverDecisionRepository,
    SfuBroadcastFailoverPolicy,
    SfuBroadcastFailoverRequest,
    SfuBroadcastFailoverService,
    SfuFailoverEpochs,
    SfuFailoverState,
    SfuParentRekeyResult,
    SfuRuntimeActivationAck,
    SfuRuntimeBinding,
    SfuScopedAdmissionToken,
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Ports:
    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.revoked = False
        self.kill = False
        self.ack_epochs = None
        self.rekey_result = SfuParentRekeyResult(True, True, 8, "parent_rekey_ok")

    def revoke_old(self, request, successor, operation_id):
        self.events.append("revoke")
        return True

    def request_rekey(self, request, minimum_key_epoch, operation_id):
        self.events.append("rekey")
        return self.rekey_result

    def issue(self, request, binding, epochs, ttl_seconds, operation_id):
        self.events.append("token")
        return SfuScopedAdmissionToken(
            "token-1", binding, epochs, self.clock.now + ttl_seconds
        )

    def activate(self, request, token, operation_id):
        self.events.append("activate")
        return SfuRuntimeActivationAck(
            True,
            token.binding,
            self.ack_epochs or token.epochs,
            "runtime_ack",
        )

    def activate_parent_fallback(self, request, operation_id):
        self.events.append("fallback")
        return True

    def kill_switch_enabled(self, tenant_id):
        return self.kill

    def authorization_revoked(self, tenant_id, room_id):
        return self.revoked


def binding(cluster="cluster-b"):
    return SfuRuntimeBinding("livekit_control_api", cluster, "eu-central", None)


def request(**changes):
    value = SfuBroadcastFailoverRequest(
        decision_id="decision-1",
        tenant_id="tenant",
        room_id="room",
        source=binding("cluster-a"),
        target=binding("cluster-b"),
        current_epochs=SfuFailoverEpochs(4, 5, 7, 9),
        rekey_required=True,
        reason_code="node_unhealthy",
    )
    return replace(value, **changes)


def build():
    clock = Clock()
    ports = Ports(clock)
    service = SfuBroadcastFailoverService(
        InMemorySfuFailoverDecisionRepository(),
        ports,
        ports,
        ports,
        ports,
        ports,
        ports,
        SfuBroadcastFailoverPolicy(retry_cooldown_seconds=1, retry_budget=1),
        clock=clock,
    )
    return service, ports, clock


def test_revoke_rekey_token_and_route_are_strictly_ordered_and_fenced():
    service, ports, _ = build()
    initial = service.start(request())
    revoked = service.advance(request(), initial)
    rekeyed = service.advance(request(), revoked)
    completed = service.advance(request(), rekeyed)
    assert completed.state is SfuFailoverState.COMPLETED
    assert completed.epochs.route_epoch == 5
    assert completed.epochs.topology_epoch == 6
    assert completed.epochs.key_epoch == 8
    assert completed.epochs.fencing_token == 10
    assert ports.events == ["revoke", "rekey", "token", "activate"]

    replay = service.start(request())
    assert replay == completed
    stale = SfuRuntimeActivationAck(
        True, binding("cluster-a"), request().current_epochs, "stale_ack"
    )
    assert service.ack_is_current(request(), completed, stale) is False


def test_parent_rekey_failure_is_bounded_and_authorization_revoke_falls_back():
    service, ports, clock = build()
    ports.rekey_result = SfuParentRekeyResult(False, False, None, "parent_rekey_timeout")
    current = service.advance(request(), service.start(request()))
    retry = service.advance(request(), current)
    assert retry.attempts == 1
    clock.now = retry.next_retry_at
    terminal = service.advance(request(), retry)
    assert terminal.state is SfuFailoverState.PARENT_FALLBACK
    assert terminal.reason_code == "sfu_failover_retry_budget_exhausted"

    second_request = replace(request(), decision_id="decision-2")
    current = service.start(second_request)
    ports.revoked = True
    revoked = service.advance(second_request, current)
    assert revoked.state is SfuFailoverState.PARENT_FALLBACK


def test_missing_target_and_kill_switch_never_loop_or_invent_native_node():
    service, ports, _ = build()
    missing = replace(request(), decision_id="missing", target=None)
    terminal = service.advance(missing, service.start(missing))
    assert terminal.state is SfuFailoverState.PARENT_FALLBACK

    killed_request = replace(request(), decision_id="killed")
    ports.kill = True
    killed = service.advance(killed_request, service.start(killed_request))
    assert killed.state is SfuFailoverState.PARENT_FALLBACK
