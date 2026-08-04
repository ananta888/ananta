from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_task_snapshot_service import (
    KnowledgeIndexTaskSnapshotDenied,
    KnowledgeIndexTaskSnapshotService,
)
from ananta_contracts.knowledge_index_dispatch import (
    build_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexAuthorityBinding,
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionJob,
    KnowledgeIndexExecutionPayload,
    KnowledgeIndexFileManifest,
    KnowledgeIndexResourceBudget,
    parse_execution_job,
)
from ananta_contracts.knowledge_index_task_snapshot import (
    MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES,
    KnowledgeIndexTaskSnapshotContractError,
    build_knowledge_index_task_snapshot,
)
from ananta_contracts.source_control import MAX_SOURCE_ADMISSION_FILES
from worker.retrieval.knowledge_index_task_snapshot import (
    HubKnowledgeIndexTaskSnapshotClient,
    KnowledgeIndexTaskSnapshotLoader,
)

_WORKER_ID = "worker-index-01"
_WORKER_URL = "http://worker-index-01:8080"
_OTHER_JOB_ID = f"knowledge-index-{'f' * 32}"


def _job(
    *,
    worker_id: str = _WORKER_ID,
    files: list[dict] | None = None,
) -> dict:
    authority = KnowledgeIndexAuthorityBinding.create(
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        source_revision_id=f"srev_{'a' * 64}",
        source_revision_digest="b" * 64,
        admission_digest="c" * 64,
        policy_snapshot_id="policy-v1",
        policy_snapshot_digest="d" * 64,
        destination_id=f"dst_{'e' * 64}",
        destination_digest="f" * 64,
        source_access_grant_id=f"grant_{'1' * 64}",
        source_access_grant_digest="2" * 64,
    )
    raw_files = files or [
        {
            "relative_path": "README.md",
            "sha256": "3" * 64,
            "size_bytes": 1,
        }
    ]
    file_manifest = KnowledgeIndexFileManifest.create(raw_files)
    total_bytes = sum(int(item["size_bytes"]) for item in raw_files)
    largest_file_bytes = max(
        int(item["size_bytes"]) for item in raw_files
    )
    return KnowledgeIndexExecutionJob.create(
        hub_task_id="hub-task-alpha",
        idempotency_key_digest="4" * 64,
        authority_binding=authority,
        file_manifest=file_manifest,
        resources=KnowledgeIndexResourceBudget(
            max_files=len(raw_files),
            max_total_bytes=max(1, total_bytes),
            max_file_bytes=max(1, largest_file_bytes),
            max_runtime_seconds=60,
            max_memory_bytes=64 * 1024 * 1024,
            max_output_bytes=1024,
        ),
        payload=KnowledgeIndexExecutionPayload(
            payload_artifact_ref={
                "artifact_id": "payload-artifact-alpha",
                "sha256": "5" * 64,
                "size_bytes": 1,
                "media_type": ("application/vnd.ananta.knowledge-index-job+json"),
                "encoding": "json",
            },
        ),
        assignment=KnowledgeIndexExecutionAssignment(
            assignment_id="assignment-alpha",
            worker_id=worker_id,
            lease_id="lease-alpha",
            lease_generation=1,
            lease_issued_epoch_ms=9_000,
            lease_expires_epoch_ms=20_000,
        ),
        scope_id="source-alpha",
        source_scope="repository",
        profile_name="default",
        created_by="owner-alpha",
        created_at_epoch_ms=9_000,
        attempt=1,
        job_type="source_records",
    ).to_wire()


def _snapshot(job: dict) -> dict:
    return build_knowledge_index_task_snapshot(
        status="todo",
        job=job,
        worker_id=job["assignment"]["worker_id"],
        worker_url=_WORKER_URL,
    )


def test_twenty_thousand_file_manifest_fits_bounded_worker_snapshot() -> None:
    files = [
        {
            "relative_path": f"{index:05d}/" + "a" * 506,
            "sha256": "3" * 64,
            "size_bytes": 0,
        }
        for index in range(MAX_SOURCE_ADMISSION_FILES)
    ]

    job = _job(files=files)
    snapshot = _snapshot(job)
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    assert len(job["file_manifest"]["files"]) == (
        MAX_SOURCE_ADMISSION_FILES
    )
    assert len(encoded) > 8 * 1024 * 1024
    assert len(encoded) <= MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES

    overflow_manifest = copy.deepcopy(job["file_manifest"])
    overflow_manifest["files"].append(
        {
            "relative_path": "overflow.py",
            "sha256": "3" * 64,
            "size_bytes": 0,
        }
    )
    with pytest.raises(ValueError):
        KnowledgeIndexFileManifest.model_validate(overflow_manifest)
    with pytest.raises(ValueError):
        KnowledgeIndexResourceBudget(
            max_files=MAX_SOURCE_ADMISSION_FILES + 1,
            max_total_bytes=1,
            max_file_bytes=1,
            max_runtime_seconds=60,
            max_memory_bytes=64 * 1024 * 1024,
            max_output_bytes=1024,
        )


def test_file_manifest_path_limit_is_measured_in_utf8_wire_bytes() -> None:
    with pytest.raises(
        ValueError,
        match="knowledge_index_manifest_path_invalid",
    ):
        KnowledgeIndexFileManifest.create(
            [
                {
                    "relative_path": "ä" * 257,
                    "sha256": "3" * 64,
                    "size_bytes": 0,
                }
            ]
        )

    accepted = KnowledgeIndexFileManifest.create(
        [
            {
                "relative_path": "ä" * 256,
                "sha256": "3" * 64,
                "size_bytes": 0,
            }
        ]
    )
    assert accepted.files[0].relative_path == "ä" * 256


def test_file_manifest_path_limit_counts_json_escaping() -> None:
    accepted = KnowledgeIndexFileManifest.create(
        [
            {
                "relative_path": '"' * 256,
                "sha256": "3" * 64,
                "size_bytes": 0,
            }
        ]
    )
    assert accepted.files[0].relative_path == '"' * 256

    with pytest.raises(
        ValueError,
        match="knowledge_index_manifest_path_invalid",
    ):
        KnowledgeIndexFileManifest.create(
            [
                {
                    "relative_path": '"' * 257,
                    "sha256": "3" * 64,
                    "size_bytes": 0,
                }
            ]
        )


class _SnapshotTransport:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = copy.deepcopy(snapshot)
        self.fetched_task_ids: list[str] = []

    def fetch(self, *, task_id: str) -> dict:
        self.fetched_task_ids.append(task_id)
        return copy.deepcopy(self.snapshot)


class _EmptyWorkerTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}

    def upsert_bound_knowledge_index_worker_snapshot(
        self,
        task_id: str,
        *,
        status: str,
        base_envelope: dict,
        worker_binding: dict,
    ) -> dict:
        task = {
            "id": task_id,
            "status": status,
            "task_kind": "codecompass_index_build",
            "worker_execution_context": {
                "knowledge_index_job": copy.deepcopy(base_envelope),
                "knowledge_index_worker_binding": copy.deepcopy(worker_binding),
            },
        }
        self.tasks[task_id] = task
        return copy.deepcopy(task)


@pytest.mark.parametrize("phase", ["propose", "execute"])
def test_empty_isolated_worker_loads_capability_free_base_before_lookup(
    phase: str,
) -> None:
    job = _job()
    transport = _SnapshotTransport(_snapshot(job))
    repository = _EmptyWorkerTaskRepository()
    marker_kwargs = {}
    if phase == "execute":
        marker_kwargs["source_access_manifest"] = {
            "assignment_id": job["assignment"]["assignment_id"],
            "lease_id": job["assignment"]["lease_id"],
        }
    marker = build_knowledge_index_dispatch(
        job_id=job["job_id"],
        phase=phase,
        **marker_kwargs,
    )

    loaded = KnowledgeIndexTaskSnapshotLoader(
        transport=transport,
        task_repository=repository,
        worker_id=_WORKER_ID,
        worker_url=_WORKER_URL,
        clock_ms=lambda: 10_000,
    ).ensure_for_dispatch(
        task_id=job["job_id"],
        raw_marker=marker,
        expected_phase=phase,
    )

    stored = repository.tasks[job["job_id"]]
    stored_job = stored["worker_execution_context"]["knowledge_index_job"]
    assert transport.fetched_task_ids == [job["job_id"]]
    assert loaded.job == job
    assert stored_job == job
    assert "source_access_enforcement_manifest" not in stored_job
    assert stored["worker_execution_context"]["knowledge_index_worker_binding"] == {
        "schema": "ananta.knowledge_index_worker_binding.v1",
        "worker_id": _WORKER_ID,
        "worker_url": _WORKER_URL,
    }


@pytest.mark.parametrize(
    ("snapshot_mutator", "task_id", "reason"),
    [
        (
            lambda value: value["task"]["worker_execution_context"]["knowledge_index_worker_binding"].update(
                worker_id="worker-index-02"
            ),
            None,
            "knowledge_index_task_snapshot_worker_mismatch",
        ),
        (
            lambda _value: None,
            _OTHER_JOB_ID,
            "knowledge_index_task_snapshot_task_mismatch",
        ),
        (
            lambda value: value["task"]["worker_execution_context"]["knowledge_index_job"].update(
                source_access_enforcement_manifest={}
            ),
            None,
            "knowledge_index_task_snapshot_job_invalid",
        ),
    ],
    ids=["wrong-worker", "wrong-job", "manifest-bearing"],
)
def test_worker_rejects_unbound_or_capability_bearing_snapshot(
    snapshot_mutator,
    task_id: str | None,
    reason: str,
) -> None:
    job = _job()
    snapshot = _snapshot(job)
    snapshot_mutator(snapshot)
    requested_task_id = task_id or job["job_id"]
    repository = _EmptyWorkerTaskRepository()
    marker = build_knowledge_index_dispatch(
        job_id=requested_task_id,
        phase="propose",
    )

    with pytest.raises(
        KnowledgeIndexTaskSnapshotContractError,
        match=reason,
    ):
        KnowledgeIndexTaskSnapshotLoader(
            transport=_SnapshotTransport(snapshot),
            task_repository=repository,
            worker_id=_WORKER_ID,
            worker_url=_WORKER_URL,
            clock_ms=lambda: 10_000,
        ).ensure_for_dispatch(
            task_id=requested_task_id,
            raw_marker=marker,
            expected_phase="propose",
        )

    assert repository.tasks == {}


class _TaskRepository:
    def __init__(self, task: dict) -> None:
        self.task = task

    def get_by_id(self, task_id: str) -> dict | None:
        if task_id != self.task["id"]:
            return None
        return copy.deepcopy(self.task)


class _LockPort:
    @contextmanager
    def mutation_lock(self, _task_id: str):
        yield True


class _BindingService:
    def __init__(self, job: dict, *, error: Exception | None = None) -> None:
        self.job = parse_execution_job(job)
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def validate_before_dispatch(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
    ):
        self.calls.append((job_id, authenticated_worker_id))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(job=self.job)


def _hub_snapshot_service(
    *,
    job: dict,
    assigned_worker_url: str = _WORKER_URL,
    binding_service: _BindingService | None = None,
) -> tuple[KnowledgeIndexTaskSnapshotService, _BindingService]:
    task = {
        "id": job["job_id"],
        "status": "todo",
        "task_kind": "codecompass_index_build",
        "assigned_agent_url": assigned_worker_url,
        "worker_execution_context": {
            "knowledge_index_job": copy.deepcopy(job),
        },
    }
    binding = binding_service or _BindingService(job)
    service = KnowledgeIndexTaskSnapshotService(
        repository_provider=lambda: SimpleNamespace(task_repo=_TaskRepository(task)),
        binding_service_provider=lambda: binding,
        lock_provider=lambda: _LockPort(),
    )
    return service, binding


def test_hub_snapshot_rejects_worker_url_outside_current_assignment() -> None:
    job = _job()
    service, binding = _hub_snapshot_service(
        job=job,
        assigned_worker_url="http://worker-index-02:8080",
    )

    with pytest.raises(
        KnowledgeIndexTaskSnapshotDenied,
        match="knowledge_index_task_snapshot_assignment_denied",
    ) as denied:
        service.snapshot_for_worker(
            task_id=job["job_id"],
            worker_id=_WORKER_ID,
            worker_url=_WORKER_URL,
        )

    assert denied.value.status_code == 404
    assert binding.calls == []


def test_snapshot_route_hides_assignment_mismatch_like_unknown(
    monkeypatch,
) -> None:
    from flask import Flask, g

    from agent.routes.tasks import knowledge_index_snapshot as route

    app = Flask(__name__)
    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        route,
        "log_audit",
        lambda event, details: audit_events.append((event, details)),
    )

    public_responses: list[tuple[int, dict]] = []
    for internal_reason in (
        "knowledge_index_task_snapshot_not_found",
        "knowledge_index_task_snapshot_assignment_denied",
        "knowledge_index_task_snapshot_worker_mismatch",
    ):
        class _DeniedService:
            @staticmethod
            def snapshot_for_worker(**_kwargs):
                raise KnowledgeIndexTaskSnapshotDenied(
                    internal_reason,
                    404,
                )

        monkeypatch.setattr(
            route,
            "get_knowledge_index_task_snapshot_service",
            lambda: _DeniedService(),
        )
        with app.test_request_context(
            "/internal/tasks/index-a/knowledge-index-base-snapshot"
        ):
            g.service_identity = {
                "worker_id": _WORKER_ID,
                "worker_url": _WORKER_URL,
            }
            response, status_code = (
                route.knowledge_index_base_snapshot.__wrapped__("index-a")
            )
            public_responses.append(
                (status_code, response.get_json())
            )

    assert all(
        response == public_responses[0]
        for response in public_responses[1:]
    )
    assert public_responses[0][0] == 404
    assert public_responses[0][1]["message"] == "not_found"
    assert public_responses[0][1]["data"]["reason_code"] == (
        "not_found"
    )
    assert audit_events[-1][1]["reason_code"] == (
        "knowledge_index_task_snapshot_worker_mismatch"
    )


def test_hub_snapshot_hides_worker_id_outside_current_assignment() -> None:
    job = _job(worker_id="worker-index-02")
    service, binding = _hub_snapshot_service(job=job)

    with pytest.raises(
        KnowledgeIndexTaskSnapshotDenied,
        match="knowledge_index_task_snapshot_worker_mismatch",
    ) as denied:
        service.snapshot_for_worker(
            task_id=job["job_id"],
            worker_id=_WORKER_ID,
            worker_url=_WORKER_URL,
        )

    assert denied.value.status_code == 404
    assert binding.calls == [(job["job_id"], _WORKER_ID)]


def test_hub_snapshot_rejects_failed_current_authority_validation() -> None:
    job = _job()
    binding = _BindingService(
        job,
        error=ValueError("knowledge_index_execution_lease_stale"),
    )
    service, _ = _hub_snapshot_service(
        job=job,
        binding_service=binding,
    )

    with pytest.raises(
        KnowledgeIndexTaskSnapshotDenied,
        match="knowledge_index_execution_lease_stale",
    ) as denied:
        service.snapshot_for_worker(
            task_id=job["job_id"],
            worker_id=_WORKER_ID,
            worker_url=_WORKER_URL,
        )

    assert denied.value.status_code == 409
    assert binding.calls == [(job["job_id"], _WORKER_ID)]


def test_worker_snapshot_absolute_deadline_rejects_slow_drip() -> None:
    now = [100.0]
    encoded = json.dumps(
        {"status": "success", "data": {"task_id": "task-alpha"}}
    ).encode("utf-8")

    class Response:
        status_code = 200
        headers = {}

        def iter_content(self, **_kwargs):
            midpoint = len(encoded) // 2
            now[0] += 0.6
            yield encoded[:midpoint]
            now[0] += 0.6
            yield encoded[midpoint:]

        def close(self):
            return None

    request_options = {}

    def get(_url, **kwargs):
        request_options.update(kwargs)
        return Response()

    client = HubKnowledgeIndexTaskSnapshotClient(
        hub_url="http://hub:5000",
        worker_id=_WORKER_ID,
        worker_url=_WORKER_URL,
        token_provider=lambda: "t" * 32,
        timeout_seconds=1.0,
        get=get,
        monotonic_clock=lambda: now[0],
    )

    with pytest.raises(
        RuntimeError,
        match="knowledge_index_task_snapshot_deadline_exceeded",
    ):
        client.fetch(task_id="task-alpha")

    assert request_options["timeout"] == (1.0, 1.0)
    assert request_options["allow_redirects"] is False
