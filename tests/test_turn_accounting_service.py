import json

import pytest

from agent.services.turn_accounting_repository_port import (
    TurnAccountingPage,
    TurnAccountingRecord,
    TurnAccountingRepositoryError,
    TurnAccountingRepositoryResult,
)
from agent.services.turn_accounting_service import (
    TurnAccountingCounters,
    TurnAccountingError,
    TurnAccountingEvent,
    TurnAccountingService,
)


class _Repository:
    def __init__(self):
        self.requests = []
        self.receipts = {}

    def ingest(self, request):
        self.requests.append(request)
        existing = self.receipts.get(request.event_digest)
        if existing is not None and existing[0] != request.request_digest:
            raise TurnAccountingRepositoryError("turn_accounting_event_conflict", 409)
        if existing is not None:
            return TurnAccountingRepositoryResult("replayed", existing[1])
        record = TurnAccountingRecord(
            request.sequence,
            request.observed_at_seconds,
            request.window_started_at_seconds,
            {
                "credential": request.credential_pseudonym,
                "tenant": request.tenant_pseudonym,
                "pool": request.pool_pseudonym,
                "room": request.room_pseudonym,
                "allocation": request.allocation_pseudonym,
                "node": request.node_pseudonym,
            },
            request.receiver_class,
            request.counters,
            ("turn_accounting_accepted",),
        )
        self.receipts[request.event_digest] = (request.request_digest, record)
        return TurnAccountingRepositoryResult("accepted", record)

    def page(self, scope, *, cursor, limit, now):
        del scope, cursor, limit, now
        return TurnAccountingPage((), None)

    def purge_expired(self, *, now, limit):
        del now, limit
        return 0


def _event(sequence=1, counters=None, **changes):
    values = dict(
        event_id=f"event-{sequence}",
        credential_id="credential-secret",
        tenant_ref="tenant-a",
        turn_pool_ref="pool-a",
        room_ref="room-a",
        allocation_ref="allocation-secret",
        receiver_class="relay_required",
        sfu_node_ref="node-a",
        turn_runtime_epoch="runtime-1",
        sequence=sequence,
        observed_at_seconds=1020,
        window_started_at_seconds=1020,
        counters=counters or TurnAccountingCounters(1, 2, 100, 200, 3, 4, 0, 0),
    )
    values.update(changes)
    return TurnAccountingEvent(**values)


def test_accounting_is_idempotent_payload_blind_and_forwards_only_pseudonyms():
    repository = _Repository()
    service = TurnAccountingService(repository, pseudonym_secret=b"a" * 32, clock=lambda: 1040)

    first = service.ingest(_event())
    replay = service.ingest(_event())

    assert first.accepted and replay.replayed
    encoded = json.dumps(first.record.public(), sort_keys=True)
    for original in (
        "credential-secret",
        "tenant-a",
        "pool-a",
        "room-a",
        "allocation-secret",
        "node-a",
    ):
        assert original not in encoded
    request = repository.requests[0]
    assert len(request.source_pseudonym) == 64
    assert all(len(value) == 24 for value in first.record.scope_pseudonyms.values())


def test_validation_and_egress_reconciliation_fail_closed():
    service = TurnAccountingService(_Repository(), pseudonym_secret=b"a" * 32, clock=lambda: 1400)

    assert service.reconcile_sfu_egress(
        turn_egress_bytes=1000,
        sfu_egress_bytes=100,
        tolerance_bytes=10,
    ).endswith("required")
    with pytest.raises(TurnAccountingError, match="window_invalid"):
        service.ingest(_event(window_started_at_seconds=1021))
    with pytest.raises(TurnAccountingError, match="scope_invalid"):
        service.ingest(_event(allocation_ref="raw allocation"))
