from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent.services.vector_index_preparation_policy import (
    DeploymentVectorIndexPreparationPolicy,
    VectorIndexPreparationPolicyError,
)
from agent.services.vector_index_task_service import (
    VectorIndexTaskService,
    VectorIndexTrustedScope,
)
from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)
from tests.vector_index_attestation_test_support import (
    TASK_SIGNER,
    TASK_VERIFIER,
)
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.lock = threading.Lock()

    def get_by_id(self, task_id: str):
        return self.rows.get(task_id)

    def get_all(self):
        return list(self.rows.values())


class _ScopeFence:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    @contextmanager
    def mutation_lock(self, task_id: str):
        with self._guard:
            scope_lock = self._locks.setdefault(
                task_id,
                threading.RLock(),
            )
        with scope_lock:
            yield True


class _ObservedScopeFence(_ScopeFence):
    def __init__(self) -> None:
        super().__init__()
        self._attempt_guard = threading.Lock()
        self._attempt_count = 0
        self.second_attempted = threading.Event()

    @contextmanager
    def mutation_lock(self, task_id: str):
        with self._attempt_guard:
            self._attempt_count += 1
            if self._attempt_count == 2:
                self.second_attempted.set()
        with super().mutation_lock(task_id) as acquired:
            yield acquired


class _Queue:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.calls: list[dict] = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)
        extra = dict(kwargs["extra_fields"])
        row = SimpleNamespace(
            id=kwargs["task_id"],
            status=kwargs["status"],
            priority=kwargs["priority"],
            task_kind=extra["task_kind"],
            assigned_agent_url=None,
            worker_execution_context=extra["worker_execution_context"],
            verification_status={},
        )
        row.model_dump = lambda row=row: {
            "id": row.id,
            "status": row.status,
            "priority": row.priority,
            "task_kind": row.task_kind,
            "assigned_agent_url": row.assigned_agent_url,
            "worker_execution_context": row.worker_execution_context,
            "verification_status": row.verification_status,
        }
        self.repository.rows[kwargs["task_id"]] = row


def _fixture(*, preparation_policy=None):
    repository = _Repository()
    queue = _Queue(repository)
    audits: list[tuple[str, dict]] = []
    status_calls: list[tuple[str, str, dict]] = []
    attempt_counter = 0

    def next_attempt_id() -> str:
        nonlocal attempt_counter
        attempt_counter += 1
        return f"dispatch-attempt-{attempt_counter:016d}"

    def update(task_id, status, **kwargs):
        status_calls.append((task_id, status, kwargs))
        row = repository.rows[task_id]
        row.status = status
        if "verification_status" in kwargs:
            row.verification_status = kwargs["verification_status"]
        if "worker_execution_context" in kwargs:
            row.worker_execution_context = kwargs["worker_execution_context"]

    def compare_and_set(
        task_id,
        status,
        *,
        expected_statuses,
        authoritative_predicate,
        **kwargs,
    ):
        with repository.lock:
            row = repository.rows[task_id]
            if (
                str(row.status).strip().lower()
                not in set(expected_statuses)
                or not authoritative_predicate(row)
            ):
                return False
            update(task_id, status, **kwargs)
            return True

    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        audit=lambda *_args: None,
    )
    scope_fence = _ScopeFence()
    service = VectorIndexTaskService(
        task_queue=queue,
        task_repository=repository,
        rollout_service=rollout,
        status_updater=update,
        status_cas_updater=compare_and_set,
        audit=lambda event, payload: audits.append((event, payload)),
        clock=lambda: 100.0,
        preparation_policy=preparation_policy,
        task_signer=TASK_SIGNER,
        scope_fence=scope_fence,
        attempt_id_factory=next_attempt_id,
    )
    return service, repository, queue, audits, status_calls


def _peer_service(
    service: VectorIndexTaskService,
    repository: _Repository,
    queue: _Queue,
    audits: list[tuple[str, dict]],
    *,
    status_cas_updater=None,
    attempt_id: str = "dispatch-attempt-9999999999999999",
) -> VectorIndexTaskService:
    return VectorIndexTaskService(
        task_queue=queue,
        task_repository=repository,
        rollout_service=service._rollout,
        status_updater=service._status_updater,
        status_cas_updater=(
            service._status_cas_updater
            if status_cas_updater is None
            else status_cas_updater
        ),
        audit=lambda event, payload: audits.append(
            (event, payload)
        ),
        clock=lambda: 100.0,
        preparation_policy=service._preparation_policy,
        task_signer=TASK_SIGNER,
        scope_fence=service._scope_fence,
        attempt_id_factory=lambda: attempt_id,
    )


def _scope(workspace: str = "workspace-a") -> VectorIndexTrustedScope:
    return VectorIndexTrustedScope(
        workspace_id=workspace,
        repository_id="repo-a",
        profile_name="default",
        domain="codecompass",
    )


def _input_ref(
    *,
    scope: VectorIndexTrustedScope | None = None,
    digest: str = "a" * 64,
) -> dict[str, str]:
    return VectorIndexArtifactLocator.locate(
        scope=scope or _scope(),
        content_sha256=digest,
    ).to_reference()


def _payload():
    return {
        "points": [
            {"point_id": "1", "vector": [1.0, 0.0], "payload": {"kind": "code"}}
        ]
    }


def _compatibility() -> dict:
    return {
        "dimensions": 2,
        "distance": "cosine",
        "provider": "test",
        "model": "v1",
        "profile": "default",
        "encoding": "float32",
        "config_hash": "config-a",
        "schema_version": "vector_store.v1",
        "manifest_hash": "manifest-a",
    }


def _preparation(
    *,
    kind: str = "codecompass_documents",
    external: bool = False,
) -> dict:
    profile = (
        "wiki_embedding_text.v1"
        if kind == "wiki_documents"
        else "codecompass-symbol-path-summary-v1"
    )
    embedding = (
        {
            "provider": "openai_compatible",
            "provider_id": "openai_compatible",
            "policy_profile": "approved-external",
            "model": "text-embedding-3-small",
            "model_version": "text-embedding-3-small",
            "dimensions": 2,
            "base_url": "https://embeddings.example.test/v1",
            "api_key_ref": "env://ANANTA_EMBEDDING_API_KEY",
            "timeout_seconds": 20,
            "external_calls_allowed": True,
            "allowed_base_urls": [
                "https://embeddings.example.test/v1"
            ],
        }
        if external
        else {
            "provider": "hash",
            "model_version": "hash-v1",
            "dimensions": 2,
            "timeout_seconds": 20,
            "external_calls_allowed": False,
            "allowed_base_urls": [],
        }
    )
    return {
        "schema": "ananta.vector_index_preparation.v1",
        "kind": kind,
        "embedding": embedding,
        "embedding_text_profile": profile,
        "retrieval_cache_state": "cache-state-v1",
    }


def _external_policy() -> DeploymentVectorIndexPreparationPolicy:
    embedding = dict(_preparation(external=True)["embedding"])
    embedding.pop("policy_profile")
    return DeploymentVectorIndexPreparationPolicy(
        {
            "approved-external": {
                "domains": ["codecompass"],
                "embedding": embedding,
            }
        }
    )


def _preparation_compatibility(
    *,
    kind: str = "codecompass_documents",
    external: bool = False,
) -> dict:
    return {
        **_compatibility(),
        "provider": (
            "openai_compatible"
            if external
            else "local_hash"
        ),
        "model": (
            "text-embedding-3-small"
            if external
            else "hash-v1"
        ),
        "profile": (
            "wiki_embedding_text.v1"
            if kind == "wiki_documents"
            else "codecompass-symbol-path-summary-v1"
        ),
    }


def _preparation_task_payload(
    *,
    kind: str = "codecompass_documents",
    external: bool = False,
) -> dict:
    scope = (
        VectorIndexTrustedScope(
            workspace_id="workspace-a",
            repository_id="wiki-source-a",
            profile_name="default",
            domain="wiki",
        )
        if kind == "wiki_documents"
        else _scope()
    )
    return {
        "input_ref": _input_ref(scope=scope),
        "preparation": _preparation(
            kind=kind,
            external=external,
        ),
        "compatibility": _preparation_compatibility(
            kind=kind,
            external=external,
        ),
        "batch_size": 128,
    }


def _completed_result(
    job_id: str,
    attempt_id: str = "dispatch-attempt-0000000000000001",
) -> dict:
    return {
        "schema": "ananta.vector_index_task_result.v1",
        "job_id": job_id,
        "attempt_id": attempt_id,
        "idempotency_key": "request-1234",
        "operation": "index",
        "status": "completed",
        "reason_code": "upsert",
        "diagnostics": {},
        "result": {"upserted": 1},
        "error": None,
    }


def _dispatch_and_admit(
    service: VectorIndexTaskService,
    repository: _Repository,
    task: dict,
    *,
    worker_url: str = "http://worker-a:5000",
) -> dict:
    repository.rows[
        task["job_id"]
    ].assigned_agent_url = worker_url
    envelope = service.issue_dispatch_attempt(
        job_id=task["job_id"],
        worker_audience=worker_url,
        phase="execute",
    )
    dispatch = dict(envelope["dispatch"])
    service.admit_dispatch_attempt(
        job_id=task["job_id"],
        attempt_id=dispatch["attempt_id"],
        sequence=dispatch["sequence"],
        phase="execute",
        worker_audience=worker_url,
    )
    return envelope


def test_hub_owns_queue_envelope_and_idempotent_retry_key() -> None:
    service, _repository, queue, audits, _status = _fixture()
    first = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    second = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )

    assert first["job_id"] == second["job_id"]
    assert len(queue.calls) == 1
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"][
        "vector_index_task"
    ]
    assert envelope["resolved_config"]["provider"] == "json"
    assert envelope["scope"]["workspace_id"] == "workspace-a"
    assert envelope["policy_decision"] == "worker_delegation_allowed"
    assert envelope["policy_source_layers"] == ["global_json_default"]
    TASK_VERIFIER.verify(envelope)
    forged = {
        **envelope,
        "payload": {
            **envelope["payload"],
            "delete_all_scope": True,
        },
    }
    with pytest.raises(
        ValueError,
        match="vector_index_task_attestation_invalid",
    ):
        TASK_VERIFIER.verify(forged)
    assert queue.calls[0]["extra_fields"]["task_kind"] == "vector_index_operation"
    assert queue.calls[0]["extra_fields"][
        "required_capabilities"
    ] == [
        "retrieval",
        "index_write",
        "vector_index_operation",
    ]
    assert (
        queue.calls[0]["event_details"]["policy_decision"]
        == "worker_delegation_allowed"
    )
    queued_audit = dict(audits[-1][1])
    assert queued_audit["policy_decision"] == "worker_delegation_allowed"
    assert queued_audit["source_layers"] == ["global_json_default"]
    assert len(queued_audit["resolved_config_hash"]) == 64
    assert all("points" not in payload for _, payload in audits)


def test_hub_serializes_mutations_per_scope_but_isolates_workspaces() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    service.submit(
        operation="rebuild",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload={**_payload(), "compatibility": _compatibility()},
        actor="admin-a",
    )
    with pytest.raises(RuntimeError, match="vector_index_task_conflict"):
        service.submit(
            operation="delete",
            trusted_scope=_scope(),
            idempotency_key="request-5678",
            payload={"point_ids": ["1"]},
            actor="admin-a",
        )

    other = service.submit(
        operation="delete",
        trusted_scope=_scope("workspace-b"),
        idempotency_key="request-5678",
        payload={"point_ids": ["1"]},
        actor="admin-a",
    )
    assert other["scope"]["workspace_id"] == "workspace-b"
    assert len(queue.calls) == 2


@pytest.mark.parametrize(
    ("second_key", "expected_error"),
    [
        ("request-1234", None),
        ("request-5678", "vector_index_task_conflict"),
    ],
)
def test_scope_fence_serializes_submit_across_service_instances(
    second_key: str,
    expected_error: str | None,
) -> None:
    service, repository, queue, audits, _status = _fixture()
    scope_fence = _ObservedScopeFence()
    service._scope_fence = scope_fence
    second_service = _peer_service(
        service,
        repository,
        queue,
        audits,
    )
    original_ingest = queue.ingest_task
    first_ingest_started = threading.Event()
    release_first_ingest = threading.Event()

    def blocking_ingest(**kwargs):
        first_ingest_started.set()
        if not release_first_ingest.wait(timeout=3):
            raise RuntimeError("test_scope_fence_release_timeout")
        original_ingest(**kwargs)

    queue.ingest_task = blocking_ingest
    accepted: list[dict] = []
    errors: list[Exception] = []

    def submit(
        candidate: VectorIndexTaskService,
        idempotency_key: str,
    ) -> None:
        try:
            accepted.append(
                candidate.submit(
                    operation="index",
                    trusted_scope=_scope(),
                    idempotency_key=idempotency_key,
                    payload=_payload(),
                    actor="admin-a",
                )
            )
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(
        target=submit,
        args=(service, "request-1234"),
    )
    second = threading.Thread(
        target=submit,
        args=(second_service, second_key),
    )
    first.start()
    try:
        assert first_ingest_started.wait(timeout=2)
        second.start()
        assert scope_fence.second_attempted.wait(timeout=2)
        assert queue.calls == []
    finally:
        release_first_ingest.set()
        first.join(timeout=3)
        if second.ident is not None:
            second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(queue.calls) == 1
    if expected_error is None:
        assert errors == []
        assert len(accepted) == 2
        assert accepted[0]["job_id"] == accepted[1]["job_id"]
    else:
        assert len(accepted) == 1
        assert [str(error) for error in errors] == [
            expected_error
        ]


def test_cancel_and_retry_reuse_same_hub_task_and_idempotency_key() -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    cancelled = service.cancel(job_id=created["job_id"], actor="admin-a")
    retried = service.retry(job_id=created["job_id"], actor="admin-a")

    assert cancelled["status"] == "cancelled"
    assert retried["status"] == "queued"
    assert retried["job_id"] == created["job_id"]
    assert retried["idempotency_key"] == "request-1234"
    assert [item[1] for item in status_calls] == ["cancelled", "todo"]
    assert repository.get_by_id(created["job_id"]) is not None


def test_scope_fence_serializes_retry_against_peer_submit() -> None:
    service, repository, queue, audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    service.cancel(
        job_id=created["job_id"],
        actor="admin-a",
    )
    scope_fence = _ObservedScopeFence()
    service._scope_fence = scope_fence
    peer = _peer_service(
        service,
        repository,
        queue,
        audits,
    )
    original_cas = service._status_cas_updater
    retry_waiting_at_cas = threading.Event()
    release_retry = threading.Event()

    def blocked_retry_cas(task_id, status, **kwargs):
        if kwargs.get("event_type") == "vector_index_task_retried":
            retry_waiting_at_cas.set()
            if not release_retry.wait(timeout=3):
                raise RuntimeError(
                    "test_scope_retry_release_timeout"
                )
        return original_cas(task_id, status, **kwargs)

    service._status_cas_updater = blocked_retry_cas
    retry_results: list[dict] = []
    submit_errors: list[Exception] = []

    def retry() -> None:
        retry_results.append(
            service.retry(
                job_id=created["job_id"],
                actor="admin-a",
            )
        )

    def submit_peer() -> None:
        try:
            peer.submit(
                operation="delete",
                trusted_scope=_scope(),
                idempotency_key="request-5678",
                payload={"point_ids": ["1"]},
                actor="admin-b",
            )
        except Exception as exc:
            submit_errors.append(exc)

    retry_thread = threading.Thread(target=retry)
    submit_thread = threading.Thread(target=submit_peer)
    retry_thread.start()
    try:
        assert retry_waiting_at_cas.wait(timeout=2)
        submit_thread.start()
        assert scope_fence.second_attempted.wait(timeout=2)
        assert len(queue.calls) == 1
    finally:
        release_retry.set()
        retry_thread.join(timeout=3)
        if submit_thread.ident is not None:
            submit_thread.join(timeout=3)

    assert not retry_thread.is_alive()
    assert not submit_thread.is_alive()
    assert [result["status"] for result in retry_results] == [
        "queued"
    ]
    assert [str(error) for error in submit_errors] == [
        "vector_index_task_conflict"
    ]
    assert len(queue.calls) == 1


def test_hub_issues_worker_bound_dispatch_and_fences_late_results() -> None:
    service, repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    repository.rows[
        created["job_id"]
    ].assigned_agent_url = "http://worker-a:5000"

    dispatched = service.issue_dispatch_attempt(
        job_id=created["job_id"],
        worker_audience="http://worker-a:5000/",
        phase="execute",
    )

    assert dispatched["dispatch"] == {
        "schema": "ananta.vector_index_task_dispatch.v1",
        "attempt_id": "dispatch-attempt-0000000000000002",
        "sequence": 1,
        "audience": "http://worker-a:5000",
        "phase": "execute",
        "issued_at": 100.0,
        "expires_at": 400.0,
    }
    TASK_VERIFIER.verify(dispatched)
    with pytest.raises(
        ValueError,
        match="vector_index_result_attempt_mismatch",
    ):
        service.accept_worker_result(
            job_id=created["job_id"],
            result=_completed_result(
                created["job_id"],
                attempt_id=created["attempt_id"],
            ),
        )

    service.admit_dispatch_attempt(
        job_id=created["job_id"],
        attempt_id=dispatched["dispatch"]["attempt_id"],
        sequence=dispatched["dispatch"]["sequence"],
        phase="execute",
        worker_audience="http://worker-a:5000",
    )
    accepted = service.accept_worker_result(
        job_id=created["job_id"],
        result=_completed_result(
            created["job_id"],
            attempt_id=dispatched["dispatch"]["attempt_id"],
        ),
    )
    assert accepted["status"] == "completed"


def test_dispatch_is_bound_to_authoritative_worker_assignment() -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    repository.rows[
        created["job_id"]
    ].assigned_agent_url = "http://worker-b:5000"

    with pytest.raises(
        RuntimeError,
        match="vector_index_task_dispatch_conflict",
    ):
        service.issue_dispatch_attempt(
            job_id=created["job_id"],
            worker_audience="http://worker-a:5000",
            phase="execute",
        )

    assert status_calls == []
    assert created["attempt_id"] == service.get_task(
        created["job_id"]
    )["attempt_id"]


def test_cancel_before_worker_admission_revokes_unconsumed_dispatch() -> None:
    service, repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    repository.rows[
        created["job_id"]
    ].assigned_agent_url = "http://worker-a:5000"
    dispatched = service.issue_dispatch_attempt(
        job_id=created["job_id"],
        worker_audience="http://worker-a:5000",
        phase="execute",
    )

    service.cancel(job_id=created["job_id"], actor="admin-a")

    with pytest.raises(
        RuntimeError,
        match="vector_index_dispatch_admission_terminal",
    ):
        service.admit_dispatch_attempt(
            job_id=created["job_id"],
            attempt_id=dispatched["dispatch"]["attempt_id"],
            sequence=dispatched["dispatch"]["sequence"],
            phase="execute",
            worker_audience="http://worker-a:5000",
        )


def test_dispatch_admission_is_single_use_and_result_is_idempotent() -> None:
    service, repository, _queue, audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    dispatched = _dispatch_and_admit(
        service,
        repository,
        created,
    )
    dispatch = dispatched["dispatch"]
    with pytest.raises(
        RuntimeError,
        match="vector_index_dispatch_admission_replay",
    ):
        service.admit_dispatch_attempt(
            job_id=created["job_id"],
            attempt_id=dispatch["attempt_id"],
            sequence=dispatch["sequence"],
            phase="execute",
            worker_audience="http://worker-a:5000",
        )
    with pytest.raises(
        RuntimeError,
        match="vector_index_task_dispatch_inflight",
    ):
        service.issue_dispatch_attempt(
            job_id=created["job_id"],
            worker_audience="http://worker-a:5000",
            phase="execute",
        )

    result = _completed_result(
        created["job_id"],
        attempt_id=dispatch["attempt_id"],
    )
    accepted = service.accept_worker_result(
        job_id=created["job_id"],
        result=result,
    )
    writes_after_first = len(status_calls)
    audits_after_first = len(audits)

    replayed = service.accept_worker_result(
        job_id=created["job_id"],
        result=result,
    )

    assert accepted["status"] == "completed"
    assert replayed == accepted
    assert len(status_calls) == writes_after_first
    assert len(audits) == audits_after_first

    conflicting = {
        **result,
        "result": {"upserted": 999},
    }
    with pytest.raises(
        RuntimeError,
        match="vector_index_result_terminal_conflict",
    ):
        service.accept_worker_result(
            job_id=created["job_id"],
            result=conflicting,
        )
    assert (
        repository.rows[
            created["job_id"]
        ].verification_status["vector_index_task_result"][
            "result"
        ]["upserted"]
        == 1
    )


def test_dispatch_reissue_cas_rejects_concurrent_admission() -> None:
    service, repository, queue, audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    repository.rows[
        created["job_id"]
    ].assigned_agent_url = "http://worker-a:5000"
    dispatched = service.issue_dispatch_attempt(
        job_id=created["job_id"],
        worker_audience="http://worker-a:5000",
        phase="execute",
    )
    original_cas = service._status_cas_updater
    peer = _peer_service(
        service,
        repository,
        queue,
        audits,
        status_cas_updater=original_cas,
    )
    reissue_waiting_at_cas = threading.Event()
    release_reissue = threading.Event()

    def blocked_reissue_cas(task_id, status, **kwargs):
        if (
            kwargs.get("event_type")
            == "vector_index_task_dispatch_issued"
        ):
            reissue_waiting_at_cas.set()
            if not release_reissue.wait(timeout=3):
                raise RuntimeError(
                    "test_dispatch_reissue_release_timeout"
                )
        return original_cas(task_id, status, **kwargs)

    service._status_cas_updater = blocked_reissue_cas
    errors: list[Exception] = []

    def reissue() -> None:
        try:
            service.issue_dispatch_attempt(
                job_id=created["job_id"],
                worker_audience="http://worker-a:5000",
                phase="execute",
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=reissue)
    thread.start()
    try:
        assert reissue_waiting_at_cas.wait(timeout=2)
        admission = peer.admit_dispatch_attempt(
            job_id=created["job_id"],
            attempt_id=dispatched["dispatch"]["attempt_id"],
            sequence=dispatched["dispatch"]["sequence"],
            phase="execute",
            worker_audience="http://worker-a:5000",
        )
    finally:
        release_reissue.set()
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert [str(error) for error in errors] == [
        "vector_index_task_dispatch_conflict"
    ]
    persisted = service._envelope(
        service._raw(
            repository.get_by_id(created["job_id"])
        )
    )
    verification = repository.rows[
        created["job_id"]
    ].verification_status
    assert persisted["dispatch"] == dispatched["dispatch"]
    assert (
        verification["vector_index_dispatch_admission"]
        == admission
    )


def test_dispatch_admission_cas_rejects_concurrent_reissue() -> None:
    service, repository, queue, audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    repository.rows[
        created["job_id"]
    ].assigned_agent_url = "http://worker-a:5000"
    dispatched = service.issue_dispatch_attempt(
        job_id=created["job_id"],
        worker_audience="http://worker-a:5000",
        phase="execute",
    )
    original_cas = service._status_cas_updater
    admission_waiting_at_cas = threading.Event()
    release_admission = threading.Event()

    def blocked_admission_cas(task_id, status, **kwargs):
        if (
            kwargs.get("event_type")
            == "vector_index_task_dispatch_admitted"
        ):
            admission_waiting_at_cas.set()
            if not release_admission.wait(timeout=3):
                raise RuntimeError(
                    "test_dispatch_admission_release_timeout"
                )
        return original_cas(task_id, status, **kwargs)

    peer = _peer_service(
        service,
        repository,
        queue,
        audits,
        status_cas_updater=blocked_admission_cas,
    )
    errors: list[Exception] = []

    def admit() -> None:
        try:
            peer.admit_dispatch_attempt(
                job_id=created["job_id"],
                attempt_id=dispatched["dispatch"][
                    "attempt_id"
                ],
                sequence=dispatched["dispatch"]["sequence"],
                phase="execute",
                worker_audience="http://worker-a:5000",
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=admit)
    thread.start()
    try:
        assert admission_waiting_at_cas.wait(timeout=2)
        reissued = service.issue_dispatch_attempt(
            job_id=created["job_id"],
            worker_audience="http://worker-a:5000",
            phase="execute",
        )
    finally:
        release_admission.set()
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert [str(error) for error in errors] == [
        "vector_index_dispatch_admission_conflict"
    ]
    assert (
        reissued["dispatch"]["attempt_id"]
        != dispatched["dispatch"]["attempt_id"]
    )
    assert (
        "vector_index_dispatch_admission"
        not in repository.rows[
            created["job_id"]
        ].verification_status
    )


def test_result_cas_is_idempotent_across_service_instances() -> None:
    service, repository, queue, audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    dispatched = _dispatch_and_admit(
        service,
        repository,
        created,
    )
    original_cas = service._status_cas_updater
    result_barrier = threading.Barrier(2)

    def concurrent_cas(task_id, status, **kwargs):
        if kwargs.get("event_type") == "vector_index_task_completed":
            result_barrier.wait(timeout=2)
        return original_cas(task_id, status, **kwargs)

    service._status_cas_updater = concurrent_cas
    second_service = _peer_service(
        service,
        repository,
        queue,
        audits,
        status_cas_updater=concurrent_cas,
    )
    result = _completed_result(
        created["job_id"],
        attempt_id=dispatched["dispatch"]["attempt_id"],
    )
    accepted: list[dict] = []
    errors: list[Exception] = []

    def accept(candidate: VectorIndexTaskService) -> None:
        try:
            accepted.append(
                candidate.accept_worker_result(
                    job_id=created["job_id"],
                    result=result,
                )
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=accept, args=(service,)),
        threading.Thread(target=accept, args=(second_service,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert [item["status"] for item in accepted] == [
        "completed",
        "completed",
    ]
    assert (
        len(
            [
                call
                for call in status_calls
                if call[2].get("event_type")
                == "vector_index_task_completed"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                event
                for event, _payload in audits
                if event == "vector_index_task_completed"
            ]
        )
        == 1
    )


def test_search_and_plaintext_secrets_never_enter_mutation_queue() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    with pytest.raises(ValueError, match="operation_invalid"):
        service.submit(
            operation="search",
            trusted_scope=_scope(),
            idempotency_key="request-1234",
            payload={},
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="plaintext_secret"):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="request-5678",
            payload={
                "points": [
                    {
                        "point_id": "1",
                        "vector": [1.0],
                        "payload": {"api_key": "secret"},
                    }
                ]
            },
            actor="admin-a",
        )
    assert queue.calls == []


def test_payload_limits_and_migration_contract_are_normalized() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    with pytest.raises(ValueError, match="batch_size_invalid"):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="request-1234",
            payload={**_payload(), "batch_size": 1001},
            actor="admin-a",
        )

    task = service.submit(
        operation="migrate",
        trusted_scope=_scope(),
        idempotency_key="migration-request-1234",
        payload={
            "input_ref": _input_ref(),
            "compatibility": _compatibility(),
            "migration": {"dry_run": True, "max_batches": 2},
            "batch_size": 1000,
        },
        actor="admin-a",
        priority="critical",
    )

    envelope = queue.calls[-1]["extra_fields"]["worker_execution_context"][
        "vector_index_task"
    ]
    assert task["priority"] == "critical"
    assert envelope["payload"]["migration"] == {
        "dry_run": True,
        "max_batches": 2,
    }
    assert envelope["payload"]["input_ref"] == _input_ref()
    TASK_VERIFIER.verify(envelope)


@pytest.mark.parametrize("operation", ["index", "refresh", "rebuild"])
def test_preparation_payload_is_normalized_into_hub_owned_task(
    operation: str,
) -> None:
    service, _repository, queue, _audits, _status = _fixture()

    task = service.submit(
        operation=operation,
        trusted_scope=_scope(),
        idempotency_key=f"preparation-{operation}-request",
        payload=_preparation_task_payload(),
        actor="admin-a",
    )

    envelope = queue.calls[0]["extra_fields"][
        "worker_execution_context"
    ]["vector_index_task"]
    normalized = envelope["payload"]
    assert task["payload_summary"]["preparation_kind"] == (
        "codecompass_documents"
    )
    assert normalized["input_ref"] == _input_ref()
    assert normalized["preparation"] == {
        "schema": "ananta.vector_index_preparation.v1",
        "kind": "codecompass_documents",
        "embedding": {
            "provider": "local_hash",
            "provider_id": "local_hash",
            "model_version": "hash-v1",
            "dimensions": 2,
            "timeout_seconds": 20,
            "external_calls_allowed": False,
            "allowed_base_urls": [],
        },
        "embedding_text_profile": (
            "codecompass-symbol-path-summary-v1"
        ),
        "retrieval_cache_state": "cache-state-v1",
    }
    assert "points" not in normalized


def test_external_preparation_preserves_only_secret_reference() -> None:
    service, _repository, queue, _audits, _status = _fixture(
        preparation_policy=_external_policy()
    )

    service.submit(
        operation="refresh",
        trusted_scope=_scope(),
        idempotency_key="external-preparation-request",
        payload=_preparation_task_payload(external=True),
        actor="admin-a",
    )

    preparation = queue.calls[0]["extra_fields"][
        "worker_execution_context"
    ]["vector_index_task"]["payload"]["preparation"]
    embedding = preparation["embedding"]
    assert embedding["provider"] == "openai_compatible"
    assert embedding["provider_id"] == "openai_compatible"
    assert embedding["policy_profile"] == "approved-external"
    assert embedding["api_key_ref"] == (
        "env://ANANTA_EMBEDDING_API_KEY"
    )
    assert "api_key" not in embedding


def test_retry_revalidates_external_embedding_policy() -> None:
    approved = _external_policy()

    class SwitchablePolicy:
        enabled = True

        def authorize(self, **kwargs):
            if not self.enabled:
                raise VectorIndexPreparationPolicyError()
            return approved.authorize(**kwargs)

    policy = SwitchablePolicy()
    service, repository, _queue, _audits, status_calls = _fixture(
        preparation_policy=policy
    )
    created = service.submit(
        operation="refresh",
        trusted_scope=_scope(),
        idempotency_key="external-retry-policy",
        payload=_preparation_task_payload(external=True),
        actor="admin-a",
    )
    service.cancel(job_id=created["job_id"], actor="admin-a")
    policy.enabled = False

    with pytest.raises(
        ValueError,
        match="^vector_index_embedding_policy_forbidden$",
    ):
        service.retry(job_id=created["job_id"], actor="admin-a")

    assert repository.rows[created["job_id"]].status == "cancelled"
    assert [status for _, status, _ in status_calls] == ["cancelled"]


@pytest.mark.parametrize(
    ("invalid_case", "reason_code"),
    [
        ("not_mapping", "vector_index_preparation_invalid"),
        ("schema", "vector_index_preparation_schema_invalid"),
        ("unknown_field", "vector_index_preparation_fields_forbidden"),
        ("kind", "vector_index_preparation_kind_invalid"),
        ("profile", "vector_index_preparation_profile_invalid"),
        (
            "dimensions_type",
            "vector_index_preparation_embedding_dimensions_invalid",
        ),
        (
            "timeout_type",
            "vector_index_preparation_embedding_timeout_invalid",
        ),
        (
            "local_external_policy",
            "vector_index_preparation_embedding_policy_invalid",
        ),
        (
            "allowed_urls_limit",
            "vector_index_preparation_embedding_allowed_urls_invalid",
        ),
        ("plaintext_secret", "vector_index_plaintext_secret_forbidden"),
        (
            "external_secret_ref",
            "vector_index_embedding_policy_forbidden",
        ),
        (
            "external_url",
            "vector_index_embedding_policy_forbidden",
        ),
        (
            "missing_input_ref",
            "vector_index_preparation_input_ref_required",
        ),
        (
            "inline_ambiguous",
            "vector_index_preparation_input_ambiguous",
        ),
        ("operation", "vector_index_preparation_operation_invalid"),
        ("compatibility_required", "vector_index_compatibility_required"),
        ("dimensions_mismatch", "dimensions_mismatch"),
        ("provider_changed", "provider_changed"),
        ("model_changed", "model_changed"),
        ("profile_changed", "profile_changed"),
        (
            "scope_mismatch",
            "vector_index_preparation_scope_mismatch",
        ),
    ],
)
def test_invalid_preparation_payload_fails_before_queueing(
    invalid_case: str,
    reason_code: str,
) -> None:
    service, _repository, queue, _audits, _status = _fixture()
    operation = "refresh"
    payload = _preparation_task_payload()
    trusted_scope = _scope()
    preparation = payload["preparation"]
    embedding = preparation["embedding"]
    compatibility = payload["compatibility"]

    if invalid_case == "not_mapping":
        payload["preparation"] = []
    elif invalid_case == "schema":
        preparation["schema"] = "ananta.vector_index_preparation.v0"
    elif invalid_case == "unknown_field":
        preparation["documents"] = []
    elif invalid_case == "kind":
        preparation["kind"] = "unsupported_documents"
    elif invalid_case == "profile":
        preparation["embedding_text_profile"] = "wrong-profile"
    elif invalid_case == "dimensions_type":
        embedding["dimensions"] = "2"
    elif invalid_case == "timeout_type":
        embedding["timeout_seconds"] = 20.0
    elif invalid_case == "local_external_policy":
        embedding["external_calls_allowed"] = True
    elif invalid_case == "allowed_urls_limit":
        embedding["allowed_base_urls"] = [
            f"https://{index}.example.test"
            for index in range(17)
        ]
    elif invalid_case == "plaintext_secret":
        embedding["api_key"] = "must-not-enter-task"
    elif invalid_case == "external_secret_ref":
        payload = _preparation_task_payload(external=True)
        payload["preparation"]["embedding"]["api_key_ref"] = (
            "inline-secret"
        )
    elif invalid_case == "external_url":
        payload = _preparation_task_payload(external=True)
        payload["preparation"]["embedding"]["base_url"] = (
            "https://[invalid"
        )
    elif invalid_case == "missing_input_ref":
        payload.pop("input_ref")
    elif invalid_case == "inline_ambiguous":
        payload["points"] = _payload()["points"]
    elif invalid_case == "operation":
        operation = "delete"
    elif invalid_case == "compatibility_required":
        payload.pop("compatibility")
    elif invalid_case == "dimensions_mismatch":
        embedding["dimensions"] = 3
    elif invalid_case == "provider_changed":
        embedding["provider_id"] = "different-provider"
    elif invalid_case == "model_changed":
        embedding["model_version"] = "hash-v2"
    elif invalid_case == "profile_changed":
        compatibility["profile"] = "different-profile"
    elif invalid_case == "scope_mismatch":
        payload = _preparation_task_payload(kind="wiki_documents")
        payload["input_ref"] = _input_ref()

    with pytest.raises(ValueError) as raised:
        service.submit(
            operation=operation,
            trusted_scope=trusted_scope,
            idempotency_key=f"invalid-preparation-{invalid_case}",
            payload=payload,
            actor="admin-a",
        )

    assert str(raised.value) == reason_code
    assert queue.calls == []


def test_wiki_preparation_requires_and_accepts_wiki_scope() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    scope = VectorIndexTrustedScope(
        workspace_id="workspace-a",
        repository_id="wiki-source-a",
        profile_name="default",
        domain="wiki",
    )

    task = service.submit(
        operation="refresh",
        trusted_scope=scope,
        idempotency_key="wiki-preparation-request",
        payload=_preparation_task_payload(kind="wiki_documents"),
        actor="admin-a",
    )

    assert task["scope"]["domain"] == "wiki"
    assert task["payload_summary"]["preparation_kind"] == (
        "wiki_documents"
    )
    assert len(queue.calls) == 1


def test_failed_migration_retry_carries_bound_checkpoint() -> None:
    service, repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="migrate",
        trusted_scope=_scope(),
        idempotency_key="migration-request-1234",
        payload={
            "input_ref": _input_ref(),
            "compatibility": _compatibility(),
            "migration": {
                "dry_run": False,
                "max_batches": 1,
            },
        },
        actor="admin-a",
    )
    scope_fingerprint = _scope().fingerprint()
    idempotency_hash = hashlib.sha256(
        b"migration-request-1234"
    ).hexdigest()
    dispatched = _dispatch_and_admit(
        service,
        repository,
        created,
    )
    service.accept_worker_result(
        job_id=created["job_id"],
        result={
            "schema": "ananta.vector_index_task_result.v1",
            "job_id": created["job_id"],
            "attempt_id": dispatched["dispatch"]["attempt_id"],
            "idempotency_key": "migration-request-1234",
            "operation": "migrate",
            "status": "failed",
            "reason_code": "migration_paused",
            "diagnostics": {},
            "result": {
                "checkpoint": {
                    "source_digest": "1" * 64,
                    "collection_name": "ananta-migration-staging",
                    "next_offset": 128,
                    "scope_fingerprint": scope_fingerprint,
                    "idempotency_key_hash": idempotency_hash,
                }
            },
            "error": "vector index worker execution failed",
        },
    )

    retried = service.retry(
        job_id=created["job_id"],
        actor="admin-a",
    )
    envelope = repository.rows[created["job_id"]].worker_execution_context[
        "vector_index_task"
    ]

    assert retried["status"] == "queued"
    assert envelope["payload"]["migration"]["checkpoint"][
        "scope_fingerprint"
    ] == scope_fingerprint
    assert envelope["payload"]["migration"]["checkpoint"][
        "idempotency_key_hash"
    ] == idempotency_hash
    assert envelope["payload"]["input_ref"] == _input_ref()
    assert (
        "vector_index_dispatch_admission"
        not in repository.rows[
            created["job_id"]
        ].verification_status
    )
    TASK_VERIFIER.verify(envelope)


def test_cancelled_task_rejects_late_worker_result() -> None:
    service, _repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    service.cancel(job_id=created["job_id"], actor="admin-a")

    with pytest.raises(RuntimeError, match="result_after_cancel"):
        service.accept_worker_result(
            job_id=created["job_id"],
            result=_completed_result(created["job_id"]),
        )


@pytest.mark.parametrize(
    ("field", "unsafe_value", "reason_code"),
    [
        (
            "diagnostics",
            {"Authorization": "redacted"},
            "vector_index_result_sensitive_key_forbidden",
        ),
        (
            "diagnostics",
            {"reason": "Bearer abcdefghijklmnop"},
            "vector_index_result_sensitive_value_forbidden",
        ),
        (
            "result",
            {"password": "not-persisted"},
            "vector_index_result_sensitive_key_forbidden",
        ),
        (
            "diagnostics",
            {"reason": "password=not-persisted"},
            "vector_index_result_sensitive_value_forbidden",
        ),
    ],
)
def test_worker_result_rejects_authorization_bearer_and_password_data(
    field: str,
    unsafe_value: dict,
    reason_code: str,
) -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    result = _completed_result(created["job_id"])
    result[field] = unsafe_value

    with pytest.raises(ValueError) as raised:
        service.accept_worker_result(
            job_id=created["job_id"],
            result=result,
        )

    row = repository.rows[created["job_id"]]
    assert str(raised.value) == reason_code
    assert row.status == "todo"
    assert row.verification_status == {}
    assert status_calls == []


@pytest.mark.parametrize(
    ("limit_case", "reason_code"),
    [
        ("depth", "vector_index_result_depth_limit_exceeded"),
        ("entries", "vector_index_result_entry_limit_exceeded"),
        ("list", "vector_index_result_list_limit_exceeded"),
        ("string", "vector_index_result_string_limit_exceeded"),
        ("total", "vector_index_result_size_limit_exceeded"),
    ],
)
def test_worker_result_rejects_each_bounded_payload_limit(
    limit_case: str,
    reason_code: str,
) -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    result = _completed_result(created["job_id"])
    if limit_case == "depth":
        nested: dict = {"value": 1}
        for _ in range(9):
            nested = {"nested": nested}
        result["diagnostics"] = nested
    elif limit_case == "entries":
        result["diagnostics"] = {
            f"counter_{index}": index for index in range(260)
        }
    elif limit_case == "list":
        result["diagnostics"] = {"counters": list(range(257))}
    elif limit_case == "string":
        result["diagnostics"] = {"reason": "€" * 683}
    else:
        result["diagnostics"] = {
            f"reason_{index}": "x" * 2000 for index in range(40)
        }

    with pytest.raises(ValueError) as raised:
        service.accept_worker_result(
            job_id=created["job_id"],
            result=result,
        )

    row = repository.rows[created["job_id"]]
    assert str(raised.value) == reason_code
    assert row.status == "todo"
    assert row.verification_status == {}
    assert status_calls == []


def test_verification_status_rejects_document_text_and_secret_metadata() -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )

    with pytest.raises(
        ValueError,
        match="vector_index_result_document_content_forbidden",
    ):
        service.accept_worker_result(
            job_id=created["job_id"],
            result=_completed_result(created["job_id"]),
            status_values={
                "verification_status": {
                    "document_text": "private source document"
                }
            },
        )
    with pytest.raises(
        ValueError,
        match="vector_index_result_sensitive_key_forbidden",
    ):
        service.accept_worker_result(
            job_id=created["job_id"],
            result=_completed_result(created["job_id"]),
            status_values={
                "verification_status": {
                    "authorization": "redacted"
                }
            },
        )

    row = repository.rows[created["job_id"]]
    assert row.status == "todo"
    assert row.verification_status == {}
    assert status_calls == []


def test_worker_error_text_is_redacted_before_verification_persistence() -> None:
    service, repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    result = _completed_result(created["job_id"])
    dispatched = _dispatch_and_admit(
        service,
        repository,
        created,
    )
    result["attempt_id"] = dispatched["dispatch"]["attempt_id"]
    result.update(
        {
            "status": "failed",
            "reason_code": "vector_index_operation_failed",
            "error": "private worker implementation detail",
        }
    )

    accepted = service.accept_worker_result(
        job_id=created["job_id"],
        result=result,
    )

    persisted = repository.rows[
        created["job_id"]
    ].verification_status["vector_index_task_result"]
    assert accepted["status"] == "failed"
    assert persisted["error"] == "vector index worker execution failed"
    assert "private worker implementation detail" not in str(
        repository.rows[created["job_id"]].verification_status
    )


def test_input_ref_requires_complete_binding_and_legacy_path_is_rejected() -> None:
    service, _repository, queue, _audits, _status = _fixture()

    with pytest.raises(ValueError, match="input_ref_sha256_required"):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="input-ref-request-a",
            payload={"input_ref": {"path": "points.json"}},
            actor="admin-a",
        )

    with pytest.raises(
        ValueError,
        match="input_ref_scope_fingerprint_required",
    ):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="input-ref-request-b",
            payload={
                "input_ref": {
                    "path": "points.json",
                    "sha256": "a" * 64,
                }
            },
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="migration_fields_forbidden"):
        service.submit(
            operation="migrate",
            trusted_scope=_scope(),
            idempotency_key="legacy-migration-request-a",
            payload={
                "input_ref": _input_ref(),
                "compatibility": _compatibility(),
                "migration": {
                    "source_path": "legacy/index.json",
                    "dry_run": True,
                },
            },
            actor="admin-a",
        )

    assert queue.calls == []


@pytest.mark.parametrize(
    ("reference", "reason"),
    [
        (
            _input_ref(scope=_scope("workspace-b")),
            "vector_index_input_ref_scope_mismatch",
        ),
        (
            {
                **_input_ref(),
                "path": (
                    "codecompass/"
                    + "0" * 64
                    + "/"
                    + "a" * 64
                    + ".json"
                ),
            },
            "vector_index_input_ref_path_mismatch",
        ),
    ],
)
def test_hub_rejects_cross_scope_or_noncanonical_input_ref_before_queue(
    reference: dict[str, str],
    reason: str,
) -> None:
    service, _repository, queue, _audits, _status = _fixture()

    with pytest.raises(ValueError) as raised:
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="scope-bound-input-reference",
            payload={"input_ref": reference},
            actor="admin-a",
        )

    assert str(raised.value) == reason
    assert queue.calls == []


def test_hub_binds_input_ref_before_embedding_policy_or_queue() -> None:
    policy_calls: list[dict] = []

    class Policy:
        def authorize(self, **kwargs):
            policy_calls.append(kwargs)
            raise AssertionError(
                "embedding policy must not inspect cross-scope input"
            )

    service, _repository, queue, _audits, _status = _fixture(
        preparation_policy=Policy(),
    )
    payload = _preparation_task_payload(external=True)
    payload["input_ref"] = _input_ref(
        scope=_scope("workspace-b")
    )

    with pytest.raises(
        ValueError,
        match="vector_index_input_ref_scope_mismatch",
    ):
        service.submit(
            operation="refresh",
            trusted_scope=_scope(),
            idempotency_key="scope-before-embedding-policy",
            payload=payload,
            actor="admin-a",
        )

    assert policy_calls == []
    assert queue.calls == []


@pytest.mark.parametrize("operation", ["refresh", "rebuild", "migrate"])
def test_mutating_replacement_operations_require_complete_compatibility(
    operation: str,
) -> None:
    service, _repository, _queue, _audits, _status = _fixture()
    payload = (
        {
            "input_ref": _input_ref(),
            "migration": {},
        }
        if operation == "migrate"
        else _payload()
    )

    with pytest.raises(ValueError, match="compatibility_required"):
        service.submit(
            operation=operation,
            trusted_scope=_scope(),
            idempotency_key=f"{operation}-compat-request",
            payload=payload,
            actor="admin-a",
        )

    with pytest.raises(ValueError, match="compatibility_incomplete"):
        service.submit(
            operation=operation,
            trusted_scope=_scope(),
            idempotency_key=f"{operation}-compat-incomplete",
            payload={**payload, "compatibility": {"dimensions": 2}},
            actor="admin-a",
        )


def test_hub_rejects_ambiguous_delete_bool_batch_and_untyped_compatibility() -> None:
    service, _repository, _queue, _audits, _status = _fixture()

    with pytest.raises(ValueError, match="delete_selector_ambiguous"):
        service.submit(
            operation="delete",
            trusted_scope=_scope(),
            idempotency_key="delete-ambiguous-request",
            payload={"point_ids": ["one"], "delete_all_scope": True},
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="delete_all_scope_invalid"):
        service.submit(
            operation="delete",
            trusted_scope=_scope(),
            idempotency_key="delete-string-bool-request",
            payload={"delete_all_scope": "false"},
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="batch_size_invalid"):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="batch-bool-request",
            payload={**_payload(), "batch_size": True},
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="compatibility_invalid"):
        service.submit(
            operation="refresh",
            trusted_scope=_scope(),
            idempotency_key="compat-untyped-request",
            payload={
                **_payload(),
                "compatibility": {
                    **_compatibility(),
                    "dimensions": "2",
                },
            },
            actor="admin-a",
        )


@pytest.mark.parametrize("winner", ["cancel", "accept"])
def test_cancel_and_result_acceptance_are_atomically_serialized(
    winner: str,
) -> None:
    service, repository, _queue, _audits, _status = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    dispatched = _dispatch_and_admit(
        service,
        repository,
        created,
    )
    original_update = service._status_cas_updater
    entered = threading.Event()
    release = threading.Event()
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def blocked_update(task_id, status, **kwargs):
        if threading.current_thread().name == winner:
            entered.set()
            assert release.wait(timeout=2)
        return original_update(task_id, status, **kwargs)

    service._status_cas_updater = blocked_update

    def cancel() -> None:
        try:
            results["cancel"] = service.cancel(
                job_id=created["job_id"],
                actor="admin-a",
            )
        except Exception as exc:
            errors["cancel"] = exc

    def accept() -> None:
        try:
            results["accept"] = service.accept_worker_result(
                job_id=created["job_id"],
                result=_completed_result(
                    created["job_id"],
                    attempt_id=dispatched["dispatch"][
                        "attempt_id"
                    ],
                ),
            )
        except Exception as exc:
            errors["accept"] = exc

    actions = {"cancel": cancel, "accept": accept}
    loser = "accept" if winner == "cancel" else "cancel"
    winner_thread = threading.Thread(
        target=actions[winner],
        name=winner,
    )
    loser_thread = threading.Thread(
        target=actions[loser],
        name=loser,
    )
    winner_thread.start()
    assert entered.wait(timeout=2)
    loser_thread.start()
    release.set()
    winner_thread.join(timeout=2)
    loser_thread.join(timeout=2)

    assert not winner_thread.is_alive()
    assert not loser_thread.is_alive()
    if winner == "cancel":
        assert results["cancel"]["status"] == "cancelled"
        assert isinstance(errors["accept"], RuntimeError)
        assert "result_after_cancel" in str(errors["accept"])
        assert (
            "vector_index_task_result"
            not in repository.rows[
                created["job_id"]
            ].verification_status
        )
    else:
        assert results["accept"]["status"] == "completed"
        assert results["cancel"]["status"] == "completed"
        assert errors == {}
        assert repository.rows[
            created["job_id"]
        ].verification_status["vector_index_task_result"][
            "status"
        ] == "completed"


def test_forwarding_uses_atomic_vector_result_acceptance(
    monkeypatch,
) -> None:
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_result_verification_service, unsloth_worker_result_service
    from agent.services import vector_index_task_service as service_module

    accepted: list[dict] = []

    class Service:
        def accept_worker_result(self, **kwargs):
            accepted.append(kwargs)
            return {"status": "completed"}

    monkeypatch.setattr(
        service_module,
        "get_vector_index_task_service",
        lambda: Service(),
    )
    monkeypatch.setattr(
        forwarding,
        "update_local_task_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generic non-atomic status update used")
        ),
    )
    monkeypatch.setattr(
        recovery_result_verification_service,
        "get_recovery_result_verification_service",
        lambda: SimpleNamespace(
            verify_and_record=lambda **_kwargs: {"status": "passed"}
        ),
    )
    monkeypatch.setattr(
        unsloth_worker_result_service,
        "get_unsloth_worker_result_projector",
        lambda: SimpleNamespace(project=lambda **_kwargs: None),
    )
    result = _completed_result("vector-index-" + "a" * 32)

    forwarding.persist_forwarded_execution(
        tid=result["job_id"],
        response=result,
        task={
            "id": result["job_id"],
            "task_kind": "vector_index_operation",
            "worker_execution_context": {
                "vector_index_task": {
                    "schema": "ananta.vector_index_task.v1",
                }
            },
            "history": [],
            "last_proposal": {},
            "verification_status": {"existing": True},
        },
        request_data=SimpleNamespace(command=None),
    )

    assert len(accepted) == 1
    assert accepted[0]["job_id"] == result["job_id"]
    assert accepted[0]["result"] == result
    assert accepted[0]["status_values"] == {}


@pytest.mark.parametrize(
    "response",
    [
        {"status": "completed"},
        {"status": "failed", "error": "forged"},
        {"schema": "another.result.v1", "status": "completed"},
        {},
    ],
)
def test_authoritative_vector_task_rejects_non_vector_worker_results_without_side_effects(
    monkeypatch,
    response,
) -> None:
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import unsloth_worker_result_service

    side_effects: list[str] = []
    monkeypatch.setattr(
        forwarding,
        "update_local_task_status",
        lambda *_args, **_kwargs: side_effects.append("status"),
    )
    monkeypatch.setattr(
        forwarding,
        "normalize_forwarded_artifacts",
        lambda **_kwargs: side_effects.append("artifacts"),
    )
    monkeypatch.setattr(
        unsloth_worker_result_service,
        "get_unsloth_worker_result_projector",
        lambda: SimpleNamespace(
            project=lambda **_kwargs: side_effects.append("unsloth")
        ),
    )
    task_id = "vector-index-" + "b" * 32
    task = {
        "id": task_id,
        "task_kind": "vector_index_operation",
        "worker_execution_context": {
            "vector_index_task": {
                "schema": "ananta.vector_index_task.v1",
            }
        },
    }

    with pytest.raises(
        ValueError,
        match="vector_index_result_schema_required",
    ):
        forwarding.persist_forwarded_execution(
            tid=task_id,
            response=response,
            task=task,
            request_data=SimpleNamespace(command=None),
        )

    assert side_effects == []


def test_non_vector_task_rejects_vector_result_schema(
    monkeypatch,
) -> None:
    from agent.services import _task_scoped_forwarding as forwarding

    updates: list[object] = []
    monkeypatch.setattr(
        forwarding,
        "update_local_task_status",
        lambda *_args, **_kwargs: updates.append(True),
    )
    result = _completed_result("vector-index-" + "c" * 32)

    with pytest.raises(
        ValueError,
        match="vector_index_result_task_domain_mismatch",
    ):
        forwarding.persist_forwarded_execution(
            tid="ordinary-task",
            response=result,
            task={
                "id": "ordinary-task",
                "task_kind": "coding",
            },
            request_data=SimpleNamespace(command=None),
        )

    assert updates == []
