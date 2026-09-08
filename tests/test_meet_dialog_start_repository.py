"""Actual parallel SQL claims and terminal compare-and-set, not process locks."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select

from agent.models.meet_dialog_start import start_fingerprints
from agent.repositories.meet_dialog_starts import SqlDialogStarts, starts
from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_starts import PAYLOAD, PRINCIPAL, RECEIPT
from tests.test_meet_dialog_starts import start_store as start_store

pytestmark = pytest.mark.timeout(45)


def _process_claim(database_url, scope, digest, barrier, results):
    engine = create_engine(database_url)
    try:
        barrier.wait(timeout=10)
        results.put(SqlDialogStarts(engine).claim(scope, digest))
    finally:
        engine.dispose()


def test_separate_spawned_hub_processes_share_one_durable_claim(store, monkeypatch):
    # The broad app fixture adds dependencies with their own `tests` package.
    # Fresh interpreters must import this checkout's test target explicitly.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    context = get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    scope, digest = start_fingerprints(PRINCIPAL, "project", "", "process-key", PAYLOAD)
    processes = [
        context.Process(target=_process_claim, args=(str(store.engine.url), scope, digest, barrier, results))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        claims = [results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        assert sum(claim.created for claim in claims) == 1
        assert len({claim.token for claim in claims}) == 1
        assert store.finish(next(claim for claim in claims if claim.created), RECEIPT)
        assert store.claim(scope, digest).receipt() == RECEIPT
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=3)
            assert not process.is_alive(), "owned SQL claim probe cleanup failed"
            if process.pid is not None:
                process.close()
        results.close()
        results.join_thread()


def test_eight_concurrent_independent_connections_elect_exactly_one_starter(store):
    barrier = Barrier(8)
    scope, digest = start_fingerprints(PRINCIPAL, "project", "", "key", PAYLOAD)

    def claim():
        barrier.wait(timeout=5)
        return SqlDialogStarts(store.engine).claim(scope, digest)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: claim(), range(8)))
    assert sum(c.created for c in claims) == 1
    assert len({c.token for c in claims}) == 1
    owner = next(c for c in claims if c.created)
    assert store.finish(owner, RECEIPT)
    assert not store.finish(owner, RECEIPT) and not store.finish(owner)
    assert not store.finish(next(c for c in claims if not c.created), RECEIPT)


@pytest.mark.parametrize("field", ["scope_key", "request_digest", "token"])
def test_foreign_claim_cannot_complete_or_fail_the_winning_operation(store, field):
    claim = store.claim(*start_fingerprints(PRINCIPAL, "project", "", "key", PAYLOAD))
    assert not store.finish(replace(claim, **{field: "other"}), RECEIPT)
    assert not store.finish(replace(claim, **{field: "other"}))
    assert store.finish(claim, RECEIPT)


def test_only_closed_receipt_metadata_is_persisted_and_survives_a_new_engine(store):
    secret = "SYNTHETIC_PRIVATE_KEY_MARKER"
    scope, digest = start_fingerprints(PRINCIPAL, "project", "parent", secret, PAYLOAD)
    claim = store.claim(scope, digest)
    with pytest.raises(MeetError, match="^meet_dialog_start_receipt_invalid$"):
        store.finish(claim, RECEIPT | {"grant": secret})
    assert store.finish(claim, RECEIPT)
    with store.engine.connect() as connection:
        rows = connection.execute(select(starts)).mappings().all()
    assert len(rows) == 1 and secret not in str(rows)
    assert set(rows[0]) == {"scope_key", "request_digest", "token", "state", "task_id", "session_id"}
    engine = create_engine(store.engine.url)
    try:
        recovered = SqlDialogStarts(engine).claim(scope, digest)
        assert not recovered.created and recovered.receipt() == RECEIPT
    finally:
        engine.dispose()
