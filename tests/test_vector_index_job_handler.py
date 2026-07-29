from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading

import pytest

from tests.vector_index_attestation_test_support import (
    TASK_SIGNER,
    TASK_VERIFIER,
)
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_job_handler import (
    VectorIndexWorkerTaskHandler,
)
from worker.retrieval.vector_index_replay_guard import (
    InMemoryVectorIndexReplayGuard,
    SqliteVectorIndexReplayGuard,
    build_vector_index_replay_guard,
    canonicalize_vector_index_worker_audience,
)

_WORKER_AUDIENCE = "http://worker-a:5000"


class _Admission:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def admit(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _envelope(operation: str = "index") -> dict:
    payload = {
        "points": [{"point_id": "1", "vector": [1.0], "payload": {}}],
        "batch_size": 128,
    }
    if operation == "delete":
        payload = {"point_ids": ["1"], "batch_size": 128}
    if operation == "migrate":
        payload = {"migration": {"dry_run": True}, "batch_size": 128}
    scope = {
        "workspace_id": "workspace-a",
        "repository_id": "repo-a",
        "profile_name": "default",
        "domain": "codecompass",
    }
    idempotency_key = "request-1234"
    request_intent = {
        "operation": operation,
        "scope": scope,
        "idempotency_key": idempotency_key,
        "payload": payload,
    }
    scope_fingerprint = VectorIndexArtifactLocator.scope_fingerprint(scope)
    envelope = {
        "schema": "ananta.vector_index_task.v1",
        "job_id": "vector-index-"
        + hashlib.sha256(
            _canonical_json(
                {
                    "scope_fingerprint": scope_fingerprint,
                    "idempotency_key": idempotency_key,
                }
            )
        ).hexdigest()[:32],
        "operation": operation,
        "scope": scope,
        "scope_fingerprint": scope_fingerprint,
        "idempotency_key": idempotency_key,
        "request_fingerprint": hashlib.sha256(
            _canonical_json(request_intent)
        ).hexdigest(),
        "resolved_config": {
            "schema": "ananta.vector_store_resolved_config.v1",
            "provider": "json",
            "config_hash": "a" * 64,
            "config": {"provider": "json"},
            "source_layers": ["global_json_default"],
        },
        "policy_decision": "worker_delegation_allowed",
        "policy_source_layers": ["global_json_default"],
        "payload": payload,
        "created_by": "test-hub",
        "created_at": 1.0,
        "dispatch": {
            "schema": "ananta.vector_index_task_dispatch.v1",
            "attempt_id": "dispatch-attempt-00000001",
            "sequence": 1,
            "audience": _WORKER_AUDIENCE,
            "phase": "execute",
            "issued_at": 5.0,
            "expires_at": 100.0,
        },
    }
    return TASK_SIGNER.attest(envelope)


def _handler(
    execution,
    *,
    replay_guard=None,
    dispatch_admission=None,
    worker_audience: str = _WORKER_AUDIENCE,
    clock=lambda: 10.0,
) -> VectorIndexWorkerTaskHandler:
    return VectorIndexWorkerTaskHandler(
        execution,
        task_verifier=TASK_VERIFIER,
        replay_guard=replay_guard
        or InMemoryVectorIndexReplayGuard(),
        dispatch_admission=dispatch_admission or _Admission(),
        worker_audience=worker_audience,
        clock=clock,
    )


def _task(envelope: dict | None = None) -> dict:
    resolved = envelope or _envelope()
    return {
        "id": resolved["job_id"],
        "worker_execution_context": {"vector_index_task": resolved},
    }


def _redispatch(
    *,
    sequence: int,
    attempt_id: str,
    phase: str = "execute",
    audience: str = _WORKER_AUDIENCE,
) -> dict:
    envelope = _envelope()
    envelope["dispatch"].update(
        {
            "attempt_id": attempt_id,
            "sequence": sequence,
            "phase": phase,
            "audience": audience,
        }
    )
    return TASK_SIGNER.attest(envelope)


def test_worker_handler_executes_exactly_one_hub_envelope() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "completed",
                "reason_code": "ok",
                "diagnostics": {"backend": "json"},
                "result": {"upserted": 1},
            }

    result = _handler(Execution()).execute(task=_task())

    assert len(calls) == 1
    assert calls[0]["operation"] == "index"
    assert calls[0]["scope"]["workspace_id"] == "workspace-a"
    assert result["status"] == "completed"
    assert result["result"] == {"upserted": 1}
    assert result["attempt_id"] == "dispatch-attempt-00000001"


def test_worker_requires_hub_admission_before_vector_mutation() -> None:
    execution_calls: list[dict] = []
    ordering: list[str] = []

    class Execution:
        def execute(self, **kwargs):
            ordering.append("execute")
            execution_calls.append(kwargs)
            return {"status": "completed"}

    class Replay:
        def consume(self, **_kwargs):
            ordering.append("replay")

    class Admission(_Admission):
        def admit(self, **kwargs) -> None:
            ordering.append("admission")
            super().admit(**kwargs)

    admission = Admission()
    result = _handler(
        Execution(),
        replay_guard=Replay(),
        dispatch_admission=admission,
    ).execute(task=_task())

    assert result["status"] == "completed"
    assert ordering == ["replay", "admission", "execute"]
    assert admission.calls == [
        {
            "job_id": _envelope()["job_id"],
            "attempt_id": "dispatch-attempt-00000001",
            "sequence": 1,
            "phase": "execute",
            "worker_audience": _WORKER_AUDIENCE,
        }
    ]
    assert len(execution_calls) == 1

    denied = _Admission(
        RuntimeError(
            "vector_index_dispatch_admission_terminal"
        )
    )
    with pytest.raises(
        RuntimeError,
        match="vector_index_dispatch_admission_terminal",
    ):
        _handler(
            Execution(),
            dispatch_admission=denied,
        ).execute(task=_task())
    assert len(execution_calls) == 1


def test_worker_handler_never_accepts_search_or_exposes_exception_text() -> None:
    class Execution:
        def execute(self, **kwargs):
            del kwargs
            raise RuntimeError("secret-token-must-not-leak")

    failed = _handler(Execution()).execute(task=_task())
    assert failed["status"] == "failed"
    assert "secret-token" not in str(failed)

    invalid = _envelope()
    invalid["operation"] = "search"
    invalid = TASK_SIGNER.attest(invalid)
    try:
        _handler(Execution()).execute(task=_task(invalid))
    except ValueError as exc:
        assert str(exc) == "vector_index_task_operation_invalid"
    else:
        raise AssertionError("search must not enter the mutation handler")


def test_worker_handler_rejects_tampered_scope_before_execution() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    envelope = _envelope()
    envelope["scope"]["workspace_id"] = "workspace-b"

    try:
        _handler(Execution()).execute(task=_task(envelope))
    except ValueError as exc:
        assert str(exc) == "vector_index_task_attestation_invalid"
    else:
        raise AssertionError("tampered scope must not reach execution")

    assert calls == []


def test_worker_handler_rejects_unsigned_forged_generic_task() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    forged = _envelope()
    forged.pop("hub_attestation")

    try:
        _handler(Execution()).execute(task=_task(forged))
    except ValueError as exc:
        assert str(exc) == "vector_index_task_attestation_invalid"
    else:
        raise AssertionError("unsigned generic task reached execution")

    assert calls == []


def test_worker_handler_rejects_outer_task_id_mismatch() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    task = {
        "id": "different-task",
        "worker_execution_context": {"vector_index_task": _envelope()},
    }

    try:
        _handler(Execution()).execute(task=task)
    except ValueError as exc:
        assert str(exc) == "vector_index_task_outer_id_mismatch"
    else:
        raise AssertionError("cross-task envelope reached execution")

    assert calls == []


def test_worker_handler_rejects_bare_signed_envelope() -> None:
    class Execution:
        def execute(self, **_kwargs):
            raise AssertionError("bare envelope reached execution")

    try:
        _handler(Execution()).execute(_envelope())
    except ValueError as exc:
        assert str(exc) == "vector_index_task_envelope_missing"
    else:
        raise AssertionError("Worker accepted work outside the Hub task system")


def test_worker_handler_rejects_attested_scope_inconsistency() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    inconsistent = _envelope()
    inconsistent["scope"]["workspace_id"] = "workspace-b"
    inconsistent = TASK_SIGNER.attest(inconsistent)

    try:
        _handler(Execution()).execute(task=_task(inconsistent))
    except ValueError as exc:
        assert str(exc) == (
            "vector_index_task_scope_fingerprint_mismatch"
        )
    else:
        raise AssertionError("inconsistent Hub scope reached execution")

    assert calls == []


def test_worker_handler_rejects_tampered_config_and_delete_scope() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    for mutate in (
        lambda envelope: envelope["resolved_config"].update(
            {
                "provider": "qdrant",
                "config": {
                    "provider": "qdrant",
                    "qdrant": {
                        "endpoint": {
                            "rest_url": "https://exfil.example.test",
                            "api_key_ref": "secretfile:///run/secrets/qdrant-api-key",
                        }
                    },
                },
            }
        ),
        lambda envelope: envelope["payload"].update(
            {"delete_all_scope": True}
        ),
    ):
        forged = _envelope()
        mutate(forged)
        try:
            _handler(Execution()).execute(task=_task(forged))
        except ValueError as exc:
            assert str(exc) == "vector_index_task_attestation_invalid"
        else:
            raise AssertionError("tampered task reached execution")

    assert calls == []


def test_worker_handler_consumes_dispatch_attempt_exactly_once() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    handler = _handler(Execution())
    task = _task()

    assert handler.execute(task=task)["status"] == "completed"
    try:
        handler.execute(task=task)
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("replayed dispatch reached execution")

    assert len(calls) == 1


def test_worker_handler_rejects_older_dispatch_after_newer_sequence() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    handler = _handler(Execution())
    newer = _redispatch(
        sequence=2,
        attempt_id="dispatch-attempt-00000002",
    )
    older = _redispatch(
        sequence=1,
        attempt_id="dispatch-attempt-00000001",
    )

    assert handler.execute(task=_task(newer))["status"] == "completed"
    try:
        handler.execute(task=_task(older))
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("stale lower sequence reached execution")

    assert len(calls) == 1


def test_worker_handler_accepts_strictly_monotonic_retry_sequence() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    handler = _handler(Execution())
    first = _redispatch(
        sequence=1,
        attempt_id="dispatch-attempt-00000001",
    )
    retry = _redispatch(
        sequence=2,
        attempt_id="dispatch-attempt-00000002",
    )

    assert handler.execute(task=_task(first))["status"] == "completed"
    assert handler.execute(task=_task(retry))["status"] == "completed"
    assert len(calls) == 2


def test_worker_handler_consumes_proposal_and_fences_its_replay() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed"}

    handler = _handler(Execution())
    proposal = _redispatch(
        sequence=1,
        attempt_id="dispatch-proposal-00000001",
        phase="propose",
    )

    first = handler.propose(task=_task(proposal))
    assert first["strategy_id"] == "deterministic_handler"
    try:
        handler.propose(task=_task(proposal))
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("proposal dispatch was replayable")

    execution = _redispatch(
        sequence=2,
        attempt_id="dispatch-execute-00000002",
    )
    assert (
        handler.execute(task=_task(execution))["status"]
        == "completed"
    )
    assert len(calls) == 1


def test_worker_handler_rejects_expired_wrong_audience_and_phase() -> None:
    class Execution:
        def execute(self, **_kwargs):
            raise AssertionError("invalid dispatch reached execution")

    expired = _envelope()
    expired["dispatch"]["expires_at"] = 9.0
    expired = TASK_SIGNER.attest(expired)
    wrong_audience = _envelope()
    wrong_audience["dispatch"]["audience"] = "http://worker-b:5000"
    wrong_audience = TASK_SIGNER.attest(wrong_audience)
    wrong_phase = _envelope()
    wrong_phase["dispatch"]["phase"] = "propose"
    wrong_phase = TASK_SIGNER.attest(wrong_phase)

    for envelope, reason in (
        (expired, "vector_index_task_dispatch_expired"),
        (wrong_audience, "vector_index_task_audience_mismatch"),
        (wrong_phase, "vector_index_task_dispatch_phase_mismatch"),
    ):
        try:
            _handler(Execution()).execute(task=_task(envelope))
        except ValueError as exc:
            assert str(exc) == reason
        else:
            raise AssertionError(f"{reason} was not enforced")


def test_worker_handler_canonicalizes_equivalent_audience_origins() -> None:
    class Execution:
        def execute(self, **_kwargs):
            return {"status": "completed"}

    envelope = _redispatch(
        sequence=1,
        attempt_id="dispatch-attempt-00000001",
        audience="HTTP://WORKER-A:80/",
    )
    result = _handler(
        Execution(),
        worker_audience="http://worker-a",
    ).execute(task=_task(envelope))

    assert result["status"] == "completed"


def test_audience_origin_canonicalization_covers_https_and_ipv6() -> None:
    assert (
        canonicalize_vector_index_worker_audience(
            "HTTPS://EXAMPLE.COM:443/"
        )
        == "https://example.com"
    )
    assert (
        canonicalize_vector_index_worker_audience(
            "http://[2001:0DB8:0:0::1]:80/"
        )
        == "http://[2001:db8::1]"
    )
    assert (
        canonicalize_vector_index_worker_audience(
            "https://[::1]:8443"
        )
        == "https://[::1]:8443"
    )


def test_worker_handler_rejects_non_origin_audiences() -> None:
    class Execution:
        def execute(self, **_kwargs):
            raise AssertionError("invalid audience reached execution")

    for audience in (
        "http://user:password@worker-a:5000",
        "http://worker-a:5000/tasks",
        "http://worker-a:5000?worker=alpha",
        "http://worker-a:5000#fragment",
        "http://worker-a:0",
        "ftp://worker-a:5000",
    ):
        envelope = _redispatch(
            sequence=1,
            attempt_id="dispatch-attempt-00000001",
            audience=audience,
        )
        try:
            _handler(Execution()).execute(task=_task(envelope))
        except ValueError as exc:
            assert str(exc) == "vector_index_task_audience_invalid"
        else:
            raise AssertionError(
                f"non-origin audience was accepted: {audience}"
            )


def test_sqlite_replay_guard_survives_guard_recreation(tmp_path) -> None:
    ledger = tmp_path / "replay.sqlite3"
    first = SqliteVectorIndexReplayGuard(ledger)
    first.consume(
        job_id="vector-index-job",
        attempt_id="dispatch-attempt-00000001",
        sequence=1,
        phase="execute",
        audience=_WORKER_AUDIENCE,
        expires_at=100.0,
    )

    second = SqliteVectorIndexReplayGuard(ledger)
    try:
        second.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=1,
            phase="execute",
            audience=_WORKER_AUDIENCE,
            expires_at=100.0,
        )
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("durable replay receipt was lost")


def test_sqlite_replay_guard_persists_monotonic_high_watermark(
    tmp_path,
) -> None:
    ledger = tmp_path / "replay.sqlite3"
    first = SqliteVectorIndexReplayGuard(ledger)
    first.consume(
        job_id="vector-index-job",
        attempt_id="dispatch-attempt-00000002",
        sequence=2,
        phase="execute",
        audience=_WORKER_AUDIENCE,
        expires_at=100.0,
    )

    restarted = SqliteVectorIndexReplayGuard(ledger)
    try:
        restarted.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=1,
            phase="propose",
            audience=_WORKER_AUDIENCE,
            expires_at=100.0,
        )
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("restart lost monotonic high watermark")


def test_sqlite_replay_guard_binds_attempt_to_dispatch_receipt(
    tmp_path,
) -> None:
    ledger = tmp_path / "replay.sqlite3"
    guard = SqliteVectorIndexReplayGuard(ledger)
    guard.consume(
        job_id="vector-index-job",
        attempt_id="dispatch-attempt-00000001",
        sequence=1,
        phase="propose",
        audience=_WORKER_AUDIENCE,
        expires_at=100.0,
    )

    try:
        guard.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=2,
            phase="execute",
            audience=_WORKER_AUDIENCE,
            expires_at=101.0,
        )
    except ValueError as exc:
        assert str(exc) == "vector_index_task_replay_detected"
    else:
        raise AssertionError("attempt ID was rebound to a different grant")


def test_sqlite_replay_guard_atomically_fences_equal_sequences(
    tmp_path,
) -> None:
    ledger = tmp_path / "replay.sqlite3"
    guards = (
        SqliteVectorIndexReplayGuard(ledger),
        SqliteVectorIndexReplayGuard(ledger),
    )
    barrier = threading.Barrier(3)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def consume(guard, attempt_id: str) -> None:
        barrier.wait()
        try:
            guard.consume(
                job_id="vector-index-job",
                attempt_id=attempt_id,
                sequence=1,
                phase="execute",
                audience=_WORKER_AUDIENCE,
                expires_at=100.0,
            )
        except ValueError as exc:
            outcome = str(exc)
        else:
            outcome = "accepted"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(
            target=consume,
            args=(guard, f"dispatch-attempt-{index:08d}"),
        )
        for index, guard in enumerate(guards, start=1)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [
        "accepted",
        "vector_index_task_replay_detected",
    ]


def test_sqlite_replay_guard_rejects_relative_and_symlink_paths(
    tmp_path,
) -> None:
    try:
        SqliteVectorIndexReplayGuard("relative-replay.sqlite3")
    except RuntimeError as exc:
        assert str(exc) == "vector_index_replay_ledger_path_invalid"
    else:
        raise AssertionError("relative replay ledger path was accepted")

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    try:
        SqliteVectorIndexReplayGuard(linked_directory / "replay.sqlite3")
    except RuntimeError as exc:
        assert str(exc) == "vector_index_replay_ledger_unsafe"
    else:
        raise AssertionError("symlinked replay ledger path was accepted")

    source = tmp_path / "source.sqlite3"
    source.write_bytes(b"not-a-database")
    file_link = tmp_path / "linked.sqlite3"
    file_link.symlink_to(source)
    try:
        SqliteVectorIndexReplayGuard(file_link)
    except RuntimeError as exc:
        assert str(exc) == "vector_index_replay_ledger_unsafe"
    else:
        raise AssertionError("symlinked replay ledger file was accepted")


def test_sqlite_replay_guard_detects_path_replacement_between_calls(
    tmp_path,
) -> None:
    ledger = tmp_path / "replay.sqlite3"
    guard = SqliteVectorIndexReplayGuard(ledger)
    displaced = tmp_path / "displaced.sqlite3"
    os.replace(ledger, displaced)
    ledger.write_bytes(b"replacement")
    ledger.chmod(0o600)

    try:
        guard.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=1,
            phase="execute",
            audience=_WORKER_AUDIENCE,
            expires_at=100.0,
        )
    except RuntimeError as exc:
        assert str(exc) == "vector_index_replay_ledger_unsafe"
    else:
        raise AssertionError("replaced replay ledger was accepted")


def test_replay_guard_rejects_non_sticky_writable_ancestor(
    tmp_path,
) -> None:
    shared_volume = tmp_path / "shared-volume"
    shared_volume.mkdir(mode=0o777)
    shared_volume.chmod(0o777)
    ledger = (
        shared_volume
        / "vector-index-replay"
        / "vector-index-task-replay.sqlite3"
    )

    with pytest.raises(
        RuntimeError,
        match="vector_index_replay_ledger_unsafe",
    ):
        build_vector_index_replay_guard(
            {
                "ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE": (
                    str(ledger)
                )
            }
        )


def test_replay_guard_creates_private_parent_under_sticky_root(
    tmp_path,
) -> None:
    shared_volume = tmp_path / "shared-volume"
    shared_volume.mkdir(mode=0o700)
    shared_volume.chmod(0o1777)
    ledger = (
        shared_volume
        / "nested"
        / "vector-index-replay"
        / "vector-index-task-replay.sqlite3"
    )

    guard = build_vector_index_replay_guard(
        {"ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE": str(ledger)}
    )

    assert isinstance(guard, SqliteVectorIndexReplayGuard)
    assert (ledger.parent.stat().st_mode & 0o777) == 0o700
    assert (ledger.stat().st_mode & 0o777) == 0o600


def test_replay_guard_detects_parent_directory_replacement(
    tmp_path,
) -> None:
    parent = tmp_path / "private"
    ledger = parent / "replay.sqlite3"
    guard = SqliteVectorIndexReplayGuard(ledger)
    displaced = tmp_path / "displaced"
    os.replace(parent, displaced)
    parent.mkdir(mode=0o700)

    with pytest.raises(
        RuntimeError,
        match="vector_index_replay_ledger_unsafe",
    ):
        guard.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=1,
            phase="execute",
            audience=_WORKER_AUDIENCE,
            expires_at=100.0,
        )


def test_replay_receipt_retention_keeps_permanent_sequence_fence(
    tmp_path,
) -> None:
    ledger = tmp_path / "replay.sqlite3"
    now = [1_000.0]
    guard = SqliteVectorIndexReplayGuard(
        ledger,
        receipt_retention_seconds=3_600.0,
        clock=lambda: now[0],
    )
    guard.consume(
        job_id="vector-index-job",
        attempt_id="dispatch-attempt-00000002",
        sequence=2,
        phase="execute",
        audience=_WORKER_AUDIENCE,
        expires_at=1_100.0,
    )

    now[0] = 5_000.0
    guard.consume(
        job_id="vector-index-other",
        attempt_id="dispatch-attempt-00000003",
        sequence=1,
        phase="execute",
        audience=_WORKER_AUDIENCE,
        expires_at=5_100.0,
    )
    with sqlite3.connect(ledger) as connection:
        exact_receipts = connection.execute(
            """
            SELECT COUNT(*)
            FROM consumed_dispatches_v2
            WHERE job_id = 'vector-index-job'
            """
        ).fetchone()[0]
        high_watermarks = connection.execute(
            """
            SELECT COUNT(*)
            FROM dispatch_high_watermarks
            WHERE job_id = 'vector-index-job'
            """
        ).fetchone()[0]
    assert exact_receipts == 0
    assert high_watermarks == 1

    restarted = SqliteVectorIndexReplayGuard(
        ledger,
        receipt_retention_seconds=3_600.0,
        clock=lambda: now[0],
    )
    with pytest.raises(
        ValueError,
        match="vector_index_task_replay_detected",
    ):
        restarted.consume(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=1,
            phase="propose",
            audience=_WORKER_AUDIENCE,
            expires_at=5_200.0,
        )


@pytest.mark.parametrize("retention", ["3599", "31536001", "nan"])
def test_replay_guard_rejects_unsafe_receipt_retention(
    tmp_path,
    retention: str,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="vector_index_replay_retention_invalid",
    ):
        build_vector_index_replay_guard(
            {
                "ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE": str(
                    tmp_path / "replay.sqlite3"
                ),
                (
                    "ANANTA_VECTOR_INDEX_TASK_REPLAY_"
                    "RECEIPT_RETENTION_SECONDS"
                ): retention,
            }
        )
