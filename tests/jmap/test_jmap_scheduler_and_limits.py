from __future__ import annotations

import threading
import time

from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapCoreLimits,
    JmapSessionDocument,
)
from agent.services.jmap_request_scheduler import (
    JmapCancellationToken,
    JmapRequestScheduler,
)
from agent.services.mail_provider_ports import MailProviderResult


def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not reached")
        time.sleep(0.005)


def test_scheduler_enforces_parallelism_backpressure_timeout_and_cancellation() -> None:
    scheduler = JmapRequestScheduler(
        maximum_concurrent_requests=1,
        maximum_queued_requests=1,
        queue_timeout_seconds=0.05,
    )
    entered = threading.Event()
    release = threading.Event()
    results = {}

    def active_work():
        entered.set()
        release.wait(timeout=2)
        return MailProviderResult(ok=True, reason_code="ok")

    active = threading.Thread(
        target=lambda: results.setdefault("active", scheduler.execute(active_work))
    )
    active.start()
    assert entered.wait(timeout=1)
    cancellation = JmapCancellationToken()
    queued = threading.Thread(
        target=lambda: results.setdefault(
            "queued",
            scheduler.execute(
                lambda: MailProviderResult(ok=True, reason_code="unexpected"),
                cancellation=cancellation,
            ),
        )
    )
    queued.start()
    _wait_for(lambda: scheduler.snapshot().queued == 1)
    full = scheduler.execute(lambda: MailProviderResult(ok=True, reason_code="unexpected"))
    assert full.reason_code == "jmap_request_queue_full"
    cancellation.cancel()
    queued.join(timeout=1)
    assert results["queued"].reason_code == "jmap_request_cancelled"
    timed_out = scheduler.execute(lambda: MailProviderResult(ok=True, reason_code="unexpected"))
    assert timed_out.reason_code == "jmap_request_queue_timeout"
    release.set()
    active.join(timeout=1)
    assert results["active"].ok is True
    assert scheduler.snapshot().active == 0

    parallel = JmapRequestScheduler(
        maximum_concurrent_requests=2,
        maximum_queued_requests=1,
        queue_timeout_seconds=1,
    )
    barrier = threading.Barrier(3)
    threads = [
        threading.Thread(
            target=lambda: parallel.execute(
                lambda: (
                    barrier.wait(timeout=1),
                    MailProviderResult(ok=True, reason_code="ok"),
                )[1]
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    _wait_for(lambda: parallel.snapshot().active == 2)
    barrier.wait(timeout=1)
    for thread in threads:
        thread.join(timeout=1)
    assert parallel.snapshot().active == 0


class _OversizedGetTransport:
    def request_json(self, **_values):
        return (
            {
                "methodResponses": [
                    [
                        "Email/get",
                        {
                            "list": [
                                {"id": "E1"},
                                {"id": "E2"},
                            ]
                        },
                        "c1",
                    ]
                ]
            },
            object(),
        )


def _session() -> JmapSessionDocument:
    return JmapSessionDocument(
        session_url="https://mail.example.test/.well-known/jmap",
        api_url="https://mail.example.test/api",
        download_url_template="",
        upload_url_template="",
        event_source_url_template="",
        provider_account_id="A1",
        server_capabilities=frozenset({JMAP_CORE_CAPABILITY, JMAP_MAIL_CAPABILITY}),
        account_capabilities=frozenset({JMAP_MAIL_CAPABILITY}),
        limits=JmapCoreLimits(
            maximum_request_bytes=100000,
            maximum_concurrent_requests=1,
            maximum_calls_per_request=1,
            maximum_objects_per_get=1,
            maximum_objects_per_set=1,
            maximum_queued_requests=1,
            request_queue_timeout_seconds=1,
        ),
        state="s1",
        trusted_origin="https://mail.example.test:443",
    )


def test_client_rejects_server_object_overflow_and_call_overflow() -> None:
    client = JmapClient(
        session=_session(),
        transport=_OversizedGetTransport(),
        authorization_headers={"Authorization": "Bearer redacted"},
    )
    objects = client.get_objects(
        object_type="Email",
        provider_account_id="A1",
        ids=("E1",),
        properties=("id",),
    )
    assert objects.reason_code == "jmap_get_object_limit_exceeded"
    from agent.services.jmap_contract_service import JmapMethodCall

    calls = client.call_many(
        (
            JmapMethodCall("Email/get", {}, "c1"),
            JmapMethodCall("Email/get", {}, "c2"),
        )
    )
    assert calls.reason_code == "jmap_max_calls_exceeded"
