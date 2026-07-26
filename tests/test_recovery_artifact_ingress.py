from __future__ import annotations

import contextlib
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.common.recovery_artifact_manifest_write_boundary import (
    RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA,
    authorize_recovery_artifact_manifest_write,
)
from agent.common.task_mutation_lock import TaskMutationLockPort
from agent.repositories import tasks as task_repository_module
from agent.services.recovery_artifact_ingress_service import (
    RecoveryArtifactIngressError,
    RecoveryArtifactIngressService,
)
from agent.services.recovery_research_workspace_artifact_service import (
    RecoveryResearchWorkspaceArtifactService,
)
from agent.services.recovery_result_verification_service import (
    RecoveryResultVerificationService,
)
from agent.services.recovery_trusted_local_artifact_adapter import (
    RecoveryTrustedLocalArtifactAdapter,
)
from agent.services.recovery_worker_artifact_publisher import (
    RecoveryWorkerArtifactPublisher,
)
from agent.services.recovery_workspace_artifact_manifest_service import (
    RecoveryWorkspaceArtifactManifestError,
    RecoveryWorkspaceArtifactManifestService,
)
from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_COUNT,
    MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES,
    RecoveryArtifactIngressContractError,
    build_recovery_artifact_ingress_manifest,
    recovery_artifact_lease_token_digest,
    validate_recovery_artifact_receipt_list,
)

TASK_ID = "recovery-child-artifact"
WORKER_ID = "worker-alpha"
WORKER_URL = "http://worker-alpha:5000"
WORKER_TOKEN = "w" * 48
LEASE_TOKEN = "lease-token-for-artifact-ingress"
REQUEST_FINGERPRINT = "f" * 64
SOURCE_TASK_ID = "aaa-source-task"


class MemoryRepository:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = {
            str(row.id): row for row in list(rows or [])
        }
        self.save_calls: list[Any] = []

    def get_by_id(self, row_id: str) -> Any | None:
        return self.rows.get(str(row_id))

    def save(self, row: Any) -> Any:
        self.rows[str(row.id)] = row
        self.save_calls.append(row)
        return row


class LockPort:
    @contextlib.contextmanager
    def mutation_locks(self, _task_ids: set[str]):
        yield True


class RecordingTaskMutationLockPort(TaskMutationLockPort):
    def __init__(self) -> None:
        engine = SimpleNamespace(
            dialect=SimpleNamespace(name="sqlite")
        )
        super().__init__(engine_provider=lambda: engine)
        self.calls: list[tuple[str, ...]] = []

    @contextlib.contextmanager
    def mutation_locks(self, task_ids: Any):
        normalized = tuple(
            sorted(
                {
                    str(task_id)
                    for task_id in task_ids
                }
            )
        )
        self.calls.append(normalized)
        with super().mutation_locks(
            set(normalized)
        ) as acquired:
            yield acquired


class LockingMemoryTaskRepository(MemoryRepository):
    def __init__(
        self,
        rows: list[Any],
        *,
        lock_port: TaskMutationLockPort,
    ) -> None:
        super().__init__(rows)
        self._lock_port = lock_port

    def save(self, row: Any) -> Any:
        task_id = str(getattr(row, "id", "") or "")
        source_task_id = str(
            getattr(row, "source_task_id", "") or ""
        )
        with self._lock_port.mutation_locks(
            {task_id, source_task_id}
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    "task_mutation_lock_unavailable"
                )
            return super().save(row)


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[dict[str, Any]] = []

    def resolve_workspace_dir_for_read(
        self,
        *,
        task: dict[str, Any],
        agent_name: str,
    ) -> Path:
        self.calls.append(
            {"task": task, "agent_name": agent_name}
        )
        return self.root


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def store_bytes(
        self,
        *,
        artifact_id: str,
        version_number: int,
        filename: str,
        content: bytes,
        media_type: str,
    ) -> dict[str, Any]:
        target = (
            self.root
            / artifact_id
            / f"v{version_number:04d}__{filename}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            "storage_path": str(target),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": media_type,
            "filename": filename,
        }


class DispatchGate:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict[str, Any]] = []
        self.evaluation_calls: list[dict[str, Any]] = []

    def admit_dispatch_lease(
        self,
        task_id: str,
        **values: Any,
    ) -> SimpleNamespace:
        self.calls.append({"task_id": task_id, **values})
        return SimpleNamespace(
            allowed=self.allowed,
            reason_code=(
                "recovery_dispatch_worker_readmitted"
                if self.allowed
                else "recovery_dispatch_worker_identity_denied"
            ),
        )

    def evaluate_task(
        self,
        task: Any,
        *,
        repos: Any,
    ) -> SimpleNamespace:
        self.evaluation_calls.append(
            {"task": task, "repos": repos}
        )
        source = repos.task_repo.get_by_id(
            str(getattr(task, "source_task_id", "") or "")
        )
        source_status = str(
            getattr(source, "status", "") or ""
        ).strip().lower()
        task_status = str(
            getattr(task, "status", "") or ""
        ).strip().lower()
        terminal = {
            "completed",
            "failed",
            "cancelled",
            "aborted",
            "timeout",
            "archived",
        }
        allowed = bool(
            source is not None
            and source_status not in terminal
            and task_status not in terminal
        )
        return SimpleNamespace(
            allowed=allowed,
            reason_code=(
                "recovery_release_gate_valid"
                if allowed
                else "recovery_dispatch_owner_terminal"
            ),
        )


def _task() -> SimpleNamespace:
    token_digest = recovery_artifact_lease_token_digest(
        LEASE_TOKEN
    )
    return SimpleNamespace(
        id=TASK_ID,
        status="in_progress",
        derivation_reason="goal_task_recovery",
        source_task_id=SOURCE_TASK_ID,
        assigned_agent_url=WORKER_URL,
        worker_execution_context={
            "workspace": {"scope_key": "goal-artifact"}
        },
        verification_spec={
            "expected_artifacts": [
                {
                    "relative_path": "result.txt",
                    "required": True,
                }
            ]
        },
        expected_artifacts=[],
        verification_status={},
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": SOURCE_TASK_ID
            },
            "recovery_dispatch_lease": {
                "schema": "ananta.recovery_dispatch_lease.v1",
                "state": "worker_admitted",
                "phase": "execute",
                "revision": 2,
                "expires_at": 2_000_000_000.0,
                "worker_url": WORKER_URL,
                "admitted_worker_url": WORKER_URL,
                "token_digest": token_digest,
                "request_fingerprint": REQUEST_FINGERPRINT,
            },
        },
    )


def _source_task() -> SimpleNamespace:
    return SimpleNamespace(
        id=SOURCE_TASK_ID,
        status="blocked_by_dependency",
    )


def _descriptor(content: bytes) -> dict[str, Any]:
    return {
        "source_index": 0,
        "kind": "workspace_file",
        "workspace_path": "result.txt",
        "relative_path": "result.txt",
        "filename": "result.txt",
        "media_type": "text/plain",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "worker_artifact_id": "worker-local-artifact",
        "worker_artifact_version_id": (
            "worker-local-version"
        ),
    }


def _receipt(
    *,
    source_index: int = 0,
    task_id: str = TASK_ID,
    manifest_digest: str = "a" * 64,
) -> dict[str, Any]:
    identity = f"{source_index + 1:032x}"
    return {
        "kind": "workspace_file",
        "task_id": task_id,
        "artifact_id": f"recovery-artifact-{identity}",
        "artifact_version_id": (
            f"recovery-artifact-version-{identity}"
        ),
        "filename": f"result-{source_index}.txt",
        "media_type": "text/plain",
        "workspace_relative_path": (
            f"result-{source_index}.txt"
        ),
        "content_hash": f"{source_index + 1:064x}",
        "size_bytes": 1,
        "provenance_summary": {
            "schema": (
                "ananta.recovery_artifact_provenance.v1"
            ),
            "authority": "hub",
            "ingress": "workspace",
            "worker_url": WORKER_URL,
            "manifest_digest": manifest_digest,
            "source_index": source_index,
        },
    }


def _manifest(
    content: bytes,
    *,
    task_id: str = TASK_ID,
    worker_url: str = WORKER_URL,
    lease_token: str = LEASE_TOKEN,
    request_fingerprint: str = REQUEST_FINGERPRINT,
    descriptor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_recovery_artifact_ingress_manifest(
        task_id=task_id,
        worker_url=worker_url,
        request_fingerprint=request_fingerprint,
        lease_token=lease_token,
        artifacts=[descriptor or _descriptor(content)],
    )


def _manifest_binding(
    *,
    lease: dict[str, Any],
    manifest_digest: str,
    artifact_count: int = 1,
    total_bytes: int = 1,
) -> dict[str, Any]:
    return {
        "schema": RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA,
        "task_id": TASK_ID,
        "lease_revision": int(lease["revision"]),
        "token_digest": str(lease["token_digest"]),
        "request_fingerprint": str(
            lease["request_fingerprint"]
        ),
        "manifest_digest": manifest_digest,
        "artifact_count": artifact_count,
        "total_bytes": total_bytes,
        "bound_at": 1_900_000_001.0,
    }


def _service_fixture(
    tmp_path: Path,
    *,
    lock_port: Any | None = None,
) -> SimpleNamespace:
    workspace = tmp_path / "project-workspaces" / "goal-artifact"
    workspace.mkdir(parents=True)
    hub_store = tmp_path / "hub-data" / "artifacts"
    task = _task()
    repos = SimpleNamespace(
        task_repo=MemoryRepository([task, _source_task()]),
        artifact_repo=MemoryRepository(),
        artifact_version_repo=MemoryRepository(),
    )
    gate = DispatchGate()
    workspace_service = WorkspaceService(workspace)
    effective_lock_port = lock_port or LockPort()
    service = RecoveryArtifactIngressService(
        repository_provider=lambda: repos,
        dispatch_gate_provider=lambda: gate,
        lock_provider=lambda: effective_lock_port,
        workspace_service_provider=lambda: workspace_service,
        artifact_store_provider=lambda: ArtifactStore(hub_store),
        now_provider=lambda: 1_900_000_000.0,
    )
    return SimpleNamespace(
        service=service,
        task=task,
        repos=repos,
        gate=gate,
        workspace=workspace,
        hub_store=hub_store,
        workspace_service=workspace_service,
    )


def _materialize(
    fixture: SimpleNamespace,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return fixture.service.materialize(
        task_id=TASK_ID,
        manifest=manifest,
        lease_token=LEASE_TOKEN,
        worker_id=WORKER_ID,
        worker_url=WORKER_URL,
        worker_token=WORKER_TOKEN,
    )


def test_valid_ingress_materializes_hub_owned_rows_from_isolated_worker_data(
    tmp_path: Path,
) -> None:
    fixture = _service_fixture(tmp_path)
    content = b"Hub-verifiable recovery result\n"
    (fixture.workspace / "result.txt").write_bytes(content)

    result = _materialize(fixture, _manifest(content))

    assert result["schema"] == (
        "ananta.recovery_artifact_receipts.v1"
    )
    assert result["replayed"] is False
    receipt = result["artifacts"][0]
    assert receipt["artifact_id"].startswith(
        "recovery-artifact-"
    )
    assert receipt["artifact_id"] != "worker-local-artifact"
    assert receipt["artifact_version_id"] != (
        "worker-local-version"
    )
    artifact = fixture.repos.artifact_repo.get_by_id(
        receipt["artifact_id"]
    )
    version = fixture.repos.artifact_version_repo.get_by_id(
        receipt["artifact_version_id"]
    )
    assert artifact.created_by == (
        "hub_recovery_artifact_ingress"
    )
    assert version.artifact_id == artifact.id
    assert Path(version.storage_path).read_bytes() == content
    binding = artifact.artifact_metadata[
        "recovery_artifact_ingress"
    ]
    assert binding["task_id"] == TASK_ID
    assert binding["worker_url"] == WORKER_URL
    assert binding["worker_artifact_id"] == (
        "worker-local-artifact"
    )
    assert fixture.gate.calls[0]["worker_token"] == WORKER_TOKEN
    assert fixture.workspace_service.calls[0]["agent_name"] == (
        WORKER_ID
    )


def test_duplicate_and_partial_replay_are_idempotent(
    tmp_path: Path,
) -> None:
    fixture = _service_fixture(tmp_path)
    content = b"idempotent content"
    (fixture.workspace / "result.txt").write_bytes(content)
    manifest = _manifest(content)

    first = _materialize(fixture, manifest)
    artifact_id = first["artifacts"][0]["artifact_id"]
    artifact = fixture.repos.artifact_repo.get_by_id(
        artifact_id
    )
    # Simulate a crash after the deterministic Version save and before the
    # Artifact.latest_version_id publication.
    artifact.latest_version_id = None
    fixture.repos.artifact_repo.rows[artifact_id] = artifact

    repaired = _materialize(fixture, manifest)
    replayed = _materialize(fixture, manifest)

    assert repaired["artifacts"] == first["artifacts"]
    assert repaired["replayed"] is True
    assert replayed["replayed"] is True
    assert len(fixture.repos.artifact_repo.rows) == 1
    assert len(fixture.repos.artifact_version_repo.rows) == 1
    assert (
        fixture.repos.artifact_repo.get_by_id(
            artifact_id
        ).latest_version_id
        == first["artifacts"][0]["artifact_version_id"]
    )
    binding = fixture.repos.task_repo.get_by_id(
        TASK_ID
    ).status_reason_details["recovery_dispatch_lease"][
        "artifact_manifest_binding"
    ]
    assert binding["manifest_digest"] == manifest["digest"]
    assert binding["artifact_count"] == 1
    assert binding["total_bytes"] == len(content)
    assert len(fixture.repos.task_repo.save_calls) == 1


def test_second_manifest_digest_on_same_lease_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _service_fixture(tmp_path)
    first_content = b"first immutable manifest"
    second_content = b"second manifest must lose"
    (fixture.workspace / "result.txt").write_bytes(first_content)
    (fixture.workspace / "second.txt").write_bytes(
        second_content
    )
    first_manifest = _manifest(first_content)
    second_descriptor = _descriptor(second_content)
    second_descriptor.update(
        {
            "workspace_path": "second.txt",
            "relative_path": "second.txt",
            "filename": "second.txt",
            "worker_artifact_id": "worker-second-artifact",
            "worker_artifact_version_id": (
                "worker-second-version"
            ),
        }
    )
    second_manifest = _manifest(
        second_content,
        descriptor=second_descriptor,
    )

    first = _materialize(fixture, first_manifest)
    with pytest.raises(
        RecoveryArtifactIngressError,
        match="recovery_artifact_manifest_conflict",
    ) as denied:
        _materialize(fixture, second_manifest)

    assert denied.value.status_code == 409
    assert len(fixture.repos.artifact_repo.rows) == 1
    assert len(fixture.repos.artifact_version_repo.rows) == 1
    binding = fixture.repos.task_repo.get_by_id(
        TASK_ID
    ).status_reason_details["recovery_dispatch_lease"][
        "artifact_manifest_binding"
    ]
    assert binding["manifest_digest"] == (
        first["manifest_digest"]
    )
    assert binding["manifest_digest"] != (
        second_manifest["digest"]
    )


def test_ingress_outer_lock_initially_fences_child_and_source(
    tmp_path: Path,
) -> None:
    lock_port = RecordingTaskMutationLockPort()
    fixture = _service_fixture(
        tmp_path,
        lock_port=lock_port,
    )
    content = b"canonical outer fence"
    (fixture.workspace / "result.txt").write_bytes(content)

    _materialize(fixture, _manifest(content))

    assert lock_port.calls[0] == tuple(
        sorted({TASK_ID, SOURCE_TASK_ID})
    )


def test_task_lease_merge_requires_exact_manifest_write_authority() -> None:
    lease = dict(
        _task().status_reason_details[
            "recovery_dispatch_lease"
        ]
    )
    binding = _manifest_binding(
        lease=lease,
        manifest_digest="a" * 64,
    )
    proposed = {
        **lease,
        "artifact_manifest_binding": binding,
    }

    unauthorized = task_repository_module._merge_dispatch_lease(
        lease,
        proposed,
        task_id=TASK_ID,
    )
    assert "artifact_manifest_binding" not in unauthorized

    with authorize_recovery_artifact_manifest_write(
        task_id=TASK_ID,
        lease=lease,
        binding=binding,
    ):
        authorized = (
            task_repository_module._merge_dispatch_lease(
                lease,
                proposed,
                task_id=TASK_ID,
            )
        )
    assert authorized["artifact_manifest_binding"] == binding


def test_task_lease_merge_keeps_existing_manifest_binding_sticky() -> None:
    lease = dict(
        _task().status_reason_details[
            "recovery_dispatch_lease"
        ]
    )
    original = _manifest_binding(
        lease=lease,
        manifest_digest="a" * 64,
    )
    lease["artifact_manifest_binding"] = original
    proposed = dict(lease)
    proposed["artifact_manifest_binding"] = {
        **original,
        "manifest_digest": "b" * 64,
    }

    merged = task_repository_module._merge_dispatch_lease(
        lease,
        proposed,
        task_id=TASK_ID,
    )

    assert merged["artifact_manifest_binding"] == original


def test_new_active_lease_revision_drops_old_manifest_binding() -> None:
    lease = dict(
        _task().status_reason_details[
            "recovery_dispatch_lease"
        ]
    )
    lease["expires_at"] = 1.0
    lease["artifact_manifest_binding"] = _manifest_binding(
        lease=lease,
        manifest_digest="a" * 64,
    )
    replacement = {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "task_id": TASK_ID,
        "source_task_id": "source-task",
        "plan_id": "plan-replacement",
        "release_epoch": "release-replacement",
        "token_digest": "b" * 64,
        "phase": "execute",
        "state": "active",
        "revision": int(lease["revision"]) + 1,
        "issued_at": 2_000_000_001.0,
        "expires_at": 4_000_000_000.0,
        "worker_url": WORKER_URL,
        "request_fingerprint": "c" * 64,
    }

    merged = task_repository_module._merge_dispatch_lease(
        lease,
        replacement,
        task_id=TASK_ID,
    )

    assert merged["revision"] == replacement["revision"]
    assert merged["state"] == "active"
    assert "artifact_manifest_binding" not in merged


def test_concurrent_second_manifest_digest_has_one_winner(
    tmp_path: Path,
) -> None:
    sqlite_engine = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite")
    )
    fixture = _service_fixture(
        tmp_path,
        lock_port=TaskMutationLockPort(
            engine_provider=lambda: sqlite_engine
        ),
    )
    first_content = b"concurrent manifest alpha"
    second_content = b"concurrent manifest beta"
    (fixture.workspace / "result.txt").write_bytes(first_content)
    (fixture.workspace / "second.txt").write_bytes(
        second_content
    )
    first_manifest = _manifest(first_content)
    second_descriptor = _descriptor(second_content)
    second_descriptor.update(
        {
            "workspace_path": "second.txt",
            "relative_path": "second.txt",
            "filename": "second.txt",
            "worker_artifact_id": "worker-concurrent-second",
            "worker_artifact_version_id": (
                "worker-concurrent-second-version"
            ),
        }
    )
    second_manifest = _manifest(
        second_content,
        descriptor=second_descriptor,
    )
    start = threading.Barrier(2)

    def publish(
        manifest: dict[str, Any],
    ) -> tuple[str, Any]:
        start.wait(timeout=5)
        try:
            return ("accepted", _materialize(fixture, manifest))
        except RecoveryArtifactIngressError as exc:
            return ("denied", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=10)
            for future in (
                executor.submit(publish, first_manifest),
                executor.submit(publish, second_manifest),
            )
        ]

    accepted = [
        value
        for state, value in outcomes
        if state == "accepted"
    ]
    denied = [
        value
        for state, value in outcomes
        if state == "denied"
    ]
    assert len(accepted) == 1
    assert len(denied) == 1
    assert denied[0].reason_code == (
        "recovery_artifact_manifest_conflict"
    )
    assert denied[0].status_code == 409
    binding = fixture.repos.task_repo.get_by_id(
        TASK_ID
    ).status_reason_details["recovery_dispatch_lease"][
        "artifact_manifest_binding"
    ]
    assert binding["manifest_digest"] == accepted[0][
        "manifest_digest"
    ]
    assert len(fixture.repos.artifact_repo.rows) == 1
    assert len(fixture.repos.artifact_version_repo.rows) == 1


def test_ingress_and_terminal_sweep_have_one_fenced_winner(
    tmp_path: Path,
) -> None:
    lock_port = RecordingTaskMutationLockPort()
    fixture = _service_fixture(
        tmp_path,
        lock_port=lock_port,
    )
    fixture.repos.task_repo = LockingMemoryTaskRepository(
        list(fixture.repos.task_repo.rows.values()),
        lock_port=lock_port,
    )
    content = b"terminal race is canonically fenced"
    (fixture.workspace / "result.txt").write_bytes(content)
    manifest = _manifest(content)
    start = threading.Barrier(2)

    def publish() -> tuple[str, Any]:
        start.wait(timeout=5)
        try:
            return ("accepted", _materialize(fixture, manifest))
        except RecoveryArtifactIngressError as exc:
            return ("denied", exc)

    def terminal_sweep() -> None:
        start.wait(timeout=5)
        with lock_port.mutation_locks(
            {TASK_ID, SOURCE_TASK_ID}
        ) as acquired:
            assert acquired
            source = fixture.repos.task_repo.get_by_id(
                SOURCE_TASK_ID
            )
            child = fixture.repos.task_repo.get_by_id(TASK_ID)
            source.status = "failed"
            child.status = "failed"
            details = dict(child.status_reason_details)
            lease = dict(details["recovery_dispatch_lease"])
            lease.update(
                {
                    "state": "revoked",
                    "revision": int(lease["revision"]) + 1,
                    "revoked_at": 1_900_000_002.0,
                    "revocation_reason": (
                        "goal_terminal:failed"
                    ),
                }
            )
            details["recovery_dispatch_lease"] = lease
            child.status_reason_details = details

    with ThreadPoolExecutor(max_workers=2) as executor:
        publication_future = executor.submit(publish)
        terminal_future = executor.submit(terminal_sweep)
        publication = publication_future.result(timeout=10)
        terminal_future.result(timeout=10)

    source = fixture.repos.task_repo.get_by_id(SOURCE_TASK_ID)
    child = fixture.repos.task_repo.get_by_id(TASK_ID)
    final_lease = child.status_reason_details[
        "recovery_dispatch_lease"
    ]
    assert source.status == "failed"
    assert child.status == "failed"
    assert final_lease["state"] == "revoked"
    assert final_lease["revocation_reason"] == (
        "goal_terminal:failed"
    )
    if publication[0] == "accepted":
        assert len(fixture.repos.artifact_repo.rows) == 1
        assert (
            "artifact_manifest_binding"
            in final_lease
        )
    else:
        assert publication[1].reason_code == (
            "recovery_dispatch_owner_terminal"
        )
        assert fixture.repos.artifact_repo.rows == {}
        assert (
            "artifact_manifest_binding"
            not in final_lease
        )


def test_ingress_rejects_hash_tampering(
    tmp_path: Path,
) -> None:
    fixture = _service_fixture(tmp_path)
    content = b"authoritative bytes"
    (fixture.workspace / "result.txt").write_bytes(content)
    descriptor = _descriptor(content)
    descriptor["sha256"] = "0" * 64

    with pytest.raises(
        RecoveryArtifactIngressError,
        match="recovery_artifact_hash_mismatch",
    ):
        _materialize(
            fixture,
            _manifest(content, descriptor=descriptor),
        )

    assert fixture.repos.artifact_repo.rows == {}
    assert fixture.repos.artifact_version_repo.rows == {}


def test_ingress_rejects_path_traversal_and_path_aliasing() -> None:
    content = b"content"
    traversal = _descriptor(content)
    traversal["workspace_path"] = "../outside.txt"
    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="workspace_path_invalid",
    ):
        _manifest(content, descriptor=traversal)

    aliased = _descriptor(content)
    aliased["relative_path"] = "claimed-result.txt"
    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="path_binding_mismatch",
    ):
        _manifest(content, descriptor=aliased)


@pytest.mark.parametrize(
    "path_alias",
    (
        "./result.txt",
        "result.txt/",
        "dir//result.txt",
    ),
)
def test_ingress_contract_rejects_noncanonical_path_alias(
    path_alias: str,
) -> None:
    content = b"canonical ingress path"
    descriptor = _descriptor(content)
    descriptor["workspace_path"] = path_alias
    descriptor["relative_path"] = path_alias

    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="workspace_path_invalid",
    ):
        _manifest(content, descriptor=descriptor)


def test_forwarded_receipt_contract_rejects_excess_count() -> None:
    receipts = [
        _receipt(source_index=index)
        for index in range(MAX_RECOVERY_ARTIFACT_COUNT + 1)
    ]

    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="receipt_count_exceeded",
    ):
        validate_recovery_artifact_receipt_list(
            receipts,
            task_id=TASK_ID,
        )


def test_forwarded_receipt_contract_rejects_padding_before_json_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ananta_contracts import recovery_artifact_ingress

    oversized = _receipt()
    oversized["untrusted_padding"] = (
        "x" * (MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES + 1)
    )
    json_dumps_calls = 0

    def forbidden_json_dump(*_args: Any, **_kwargs: Any) -> str:
        nonlocal json_dumps_calls
        json_dumps_calls += 1
        raise AssertionError("untrusted padding reached json.dumps")

    monkeypatch.setattr(
        recovery_artifact_ingress.json,
        "dumps",
        forbidden_json_dump,
    )

    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="receipt_fields_invalid",
    ):
        validate_recovery_artifact_receipt_list(
            [oversized],
            task_id=TASK_ID,
        )
    assert json_dumps_calls == 0


def test_forwarded_receipt_contract_bounds_known_scalar_before_json_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ananta_contracts import recovery_artifact_ingress

    oversized = _receipt()
    oversized["filename"] = "x" * (
        MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES + 1
    )
    json_dumps_calls = 0

    def forbidden_json_dump(*_args: Any, **_kwargs: Any) -> str:
        nonlocal json_dumps_calls
        json_dumps_calls += 1
        raise AssertionError("oversized scalar reached json.dumps")

    monkeypatch.setattr(
        recovery_artifact_ingress.json,
        "dumps",
        forbidden_json_dump,
    )

    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="receipt_scalar_invalid",
    ):
        validate_recovery_artifact_receipt_list(
            [oversized],
            task_id=TASK_ID,
        )
    assert json_dumps_calls == 0


def test_forwarded_receipt_contract_is_closed() -> None:
    receipt = _receipt()
    receipt["worker_verdict"] = "passed"

    with pytest.raises(
        RecoveryArtifactIngressContractError,
        match="receipt_fields_invalid",
    ):
        validate_recovery_artifact_receipt_list(
            [receipt],
            task_id=TASK_ID,
        )


def test_manifest_rejects_excess_claims_before_workspace_io(
    tmp_path: Path,
) -> None:
    workspace_service = WorkspaceService(tmp_path)

    class Reader:
        calls = 0

        def read(self, **_values: Any) -> Any:
            self.calls += 1
            raise AssertionError("file read must not occur")

    reader = Reader()
    service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: workspace_service,
        workspace_file_reader_provider=lambda: reader,
    )

    with pytest.raises(
        RecoveryWorkspaceArtifactManifestError,
        match="recovery_artifact_count_invalid",
    ):
        service.build(
            task={"id": TASK_ID},
            artifacts=[
                {"workspace_relative_path": f"result-{index}.txt"}
                for index in range(MAX_RECOVERY_ARTIFACT_COUNT + 1)
            ],
            lease_token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
            executor_id=WORKER_ID,
            executor_url=WORKER_URL,
        )

    assert workspace_service.calls == []
    assert reader.calls == 0


def test_manifest_reader_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import (
        recovery_workspace_artifact_manifest_service as manifest_module,
    )

    content = b"12345"
    (tmp_path / "result.txt").write_bytes(content)
    monkeypatch.setattr(
        manifest_module,
        "MAX_RECOVERY_ARTIFACT_BYTES",
        4,
    )
    service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: WorkspaceService(
            tmp_path
        ),
    )

    with pytest.raises(
        RecoveryWorkspaceArtifactManifestError,
        match="recovery_artifact_file_type_or_size_invalid",
    ):
        service.build(
            task={"id": TASK_ID},
            artifacts=[
                {
                    "kind": "workspace_file",
                    "workspace_relative_path": "result.txt",
                    "content_hash": hashlib.sha256(
                        content
                    ).hexdigest(),
                }
            ],
            lease_token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
            executor_id=WORKER_ID,
            executor_url=WORKER_URL,
        )


def test_manifest_reader_rejects_file_growth_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import recovery_workspace_file_reader

    path = tmp_path / "result.txt"
    content = b"initial"
    path.write_bytes(content)
    original_read = recovery_workspace_file_reader.os.read
    mutated = False

    def growing_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            with path.open("ab") as stream:
                stream.write(b"-growth")
        return original_read(descriptor, count)

    monkeypatch.setattr(
        recovery_workspace_file_reader.os,
        "read",
        growing_read,
    )
    service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: WorkspaceService(
            tmp_path
        ),
    )

    with pytest.raises(
        RecoveryWorkspaceArtifactManifestError,
        match="recovery_artifact_file_changed_during_read",
    ):
        service.build(
            task={"id": TASK_ID},
            artifacts=[
                {
                    "kind": "workspace_file",
                    "workspace_relative_path": "result.txt",
                    "content_hash": hashlib.sha256(
                        content
                    ).hexdigest(),
                }
            ],
            lease_token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
            executor_id=WORKER_ID,
            executor_url=WORKER_URL,
        )


def test_manifest_reader_fails_closed_without_open_fd_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import recovery_workspace_file_reader

    path = tmp_path / "result.txt"
    content = b"fd identity must be observable"
    path.write_bytes(content)
    original_readlink = recovery_workspace_file_reader.os.readlink

    def missing_fd_identity(value: Any) -> str:
        if str(value).startswith("/proc/self/fd/"):
            raise FileNotFoundError(str(value))
        return original_readlink(value)

    monkeypatch.setattr(
        recovery_workspace_file_reader.os,
        "readlink",
        missing_fd_identity,
    )
    service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: WorkspaceService(
            tmp_path
        ),
    )

    with pytest.raises(
        RecoveryWorkspaceArtifactManifestError,
        match=(
            "recovery_artifact_open_file_identity_unavailable"
        ),
    ):
        service.build(
            task={"id": TASK_ID},
            artifacts=[
                {
                    "kind": "workspace_file",
                    "workspace_relative_path": "result.txt",
                    "content_hash": hashlib.sha256(
                        content
                    ).hexdigest(),
                }
            ],
            lease_token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
            executor_id=WORKER_ID,
            executor_url=WORKER_URL,
        )


@pytest.mark.parametrize(
    ("manifest_values", "call_values", "reason"),
    [
        (
            {"task_id": "other-recovery-task"},
            {},
            "recovery_artifact_task_mismatch",
        ),
        (
            {"worker_url": "http://worker-beta:5000"},
            {},
            "recovery_artifact_worker_mismatch",
        ),
        (
            {"lease_token": "different-lease-token"},
            {},
            "recovery_artifact_lease_binding_mismatch",
        ),
        (
            {
                "request_fingerprint": "e" * 64,
            },
            {},
            "recovery_artifact_lease_binding_mismatch",
        ),
    ],
)
def test_ingress_rejects_task_assignment_and_lease_tampering(
    tmp_path: Path,
    manifest_values: dict[str, Any],
    call_values: dict[str, Any],
    reason: str,
) -> None:
    del call_values
    fixture = _service_fixture(tmp_path)
    content = b"bound content"
    (fixture.workspace / "result.txt").write_bytes(content)
    manifest = _manifest(content, **manifest_values)

    with pytest.raises(
        RecoveryArtifactIngressError,
        match=reason,
    ):
        _materialize(fixture, manifest)

    assert fixture.repos.artifact_repo.rows == {}


class PassingVerification:
    def verify_from_artifacts(
        self,
        *,
        artifacts: list[dict[str, Any]],
        **_values: Any,
    ) -> dict[str, Any]:
        passed = bool(artifacts) and all(
            value.get("_exists")
            and value.get("_hash_verified")
            for value in artifacts
        )
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
        }

    def create_or_update_record(
        self,
        task_id: str,
        **_values: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=f"verification-{task_id}",
            status="passed",
        )


class ArtifactGrounding:
    def verify(
        self,
        *,
        artifacts: list[dict[str, Any]],
        **_values: Any,
    ) -> dict[str, Any]:
        passed = any(
            value.get("_hash_verified")
            for value in artifacts
        )
        return {
            "passed": passed,
            "reason_code": (
                "recovery_artifact_evidence_verified"
                if passed
                else "recovery_result_evidence_missing"
            ),
        }


def test_hub_ingress_receipt_passes_result_verification_end_to_end(
    tmp_path: Path,
) -> None:
    from agent.services._task_scoped_forwarding import (
        normalize_recovery_forwarded_artifacts,
    )

    fixture = _service_fixture(tmp_path)
    content = b"verified ingress evidence"
    (fixture.workspace / "result.txt").write_bytes(content)
    raw_receipts = _materialize(
        fixture,
        _manifest(content),
    )["artifacts"]
    receipt = normalize_recovery_forwarded_artifacts(
        task_id=TASK_ID,
        artifacts=raw_receipts,
    )
    assert receipt is not None
    verifier = RecoveryResultVerificationService(
        repository_provider=lambda: fixture.repos,
        verification_service_provider=PassingVerification,
        grounding_verification_service_provider=ArtifactGrounding,
    )

    result = verifier.verify_and_record(
        task_id=TASK_ID,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": "",
        },
        artifacts=receipt,
        publish_failure_status=False,
    )

    assert result is not None
    assert result["status"] == "passed"
    assert result["artifacts"][0]["_exists"] is True
    assert result["artifacts"][0]["_hash_verified"] is True


def test_trusted_local_adapter_materializes_once_and_passes_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.config import settings
    from agent.services._task_scoped_forwarding import (
        normalize_recovery_forwarded_artifacts,
    )

    fixture = _service_fixture(tmp_path)
    content = b"trusted local recovery evidence"
    (fixture.workspace / "result.txt").write_bytes(content)
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_can_be_worker", True)
    monkeypatch.setattr(settings, "agent_name", WORKER_ID)
    monkeypatch.setattr(settings, "agent_url", WORKER_URL)
    manifest_service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: fixture.workspace_service,
    )
    adapter = RecoveryTrustedLocalArtifactAdapter(
        manifest_service_provider=lambda: manifest_service,
        ingress_service_provider=lambda: fixture.service,
    )
    claim = {
        "kind": "workspace_file",
        "task_id": TASK_ID,
        "filename": "result.txt",
        "media_type": "text/plain",
        "workspace_relative_path": "result.txt",
        "content_hash": hashlib.sha256(content).hexdigest(),
    }

    first = adapter.materialize(
        task=dict(vars(fixture.task)),
        artifacts=[claim],
        lease_token=LEASE_TOKEN,
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    replay = adapter.materialize(
        task=dict(vars(fixture.task)),
        artifacts=[claim],
        lease_token=LEASE_TOKEN,
        request_fingerprint=REQUEST_FINGERPRINT,
    )

    assert first == replay
    assert first is not None
    assert len(fixture.repos.artifact_repo.rows) == 1
    assert len(fixture.repos.artifact_version_repo.rows) == 1
    assert fixture.gate.calls == []
    artifact = fixture.repos.artifact_repo.get_by_id(
        first[0]["artifact_id"]
    )
    binding = artifact.artifact_metadata[
        "recovery_artifact_ingress"
    ]
    assert binding["materialization_channel"] == "trusted_local"
    assert binding["task_id"] == TASK_ID
    assert binding["request_fingerprint"] == REQUEST_FINGERPRINT
    assert binding["worker_artifact_id"] is None
    # Manifest preparation and authoritative ingress independently resolve and
    # read the same workspace; no legacy Artifact upload is involved.
    assert len(fixture.workspace_service.calls) == 4

    normalized = normalize_recovery_forwarded_artifacts(
        task_id=TASK_ID,
        artifacts=first,
    )
    verifier = RecoveryResultVerificationService(
        repository_provider=lambda: fixture.repos,
        verification_service_provider=PassingVerification,
        grounding_verification_service_provider=ArtifactGrounding,
    )
    result = verifier.verify_and_record(
        task_id=TASK_ID,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": "",
        },
        artifacts=normalized,
        publish_failure_status=False,
    )

    assert result is not None
    assert result["status"] == "passed"
    assert result["artifacts"][0]["_exists"] is True
    assert result["artifacts"][0]["_hash_verified"] is True


@pytest.mark.parametrize(
    "mutation",
    ("task", "lease_request", "hash"),
)
def test_trusted_local_adapter_is_task_lease_and_hash_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from agent.config import settings

    fixture = _service_fixture(tmp_path)
    content = b"bound trusted local evidence"
    (fixture.workspace / "result.txt").write_bytes(content)
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_can_be_worker", True)
    monkeypatch.setattr(settings, "agent_name", WORKER_ID)
    monkeypatch.setattr(settings, "agent_url", WORKER_URL)
    manifest_service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: fixture.workspace_service,
    )
    adapter = RecoveryTrustedLocalArtifactAdapter(
        manifest_service_provider=lambda: manifest_service,
        ingress_service_provider=lambda: fixture.service,
    )
    task = dict(vars(fixture.task))
    request_fingerprint = REQUEST_FINGERPRINT
    content_hash = hashlib.sha256(content).hexdigest()
    if mutation == "task":
        task["id"] = "different-recovery-child"
    elif mutation == "lease_request":
        request_fingerprint = "e" * 64
    else:
        content_hash = "0" * 64

    with pytest.raises(
        RuntimeError,
        match="recovery_artifact_",
    ):
        adapter.materialize(
            task=task,
            artifacts=[
                {
                    "kind": "workspace_file",
                    "filename": "result.txt",
                    "media_type": "text/plain",
                    "workspace_relative_path": "result.txt",
                    "content_hash": content_hash,
                }
            ],
            lease_token=LEASE_TOKEN,
            request_fingerprint=request_fingerprint,
        )

    assert fixture.repos.artifact_repo.rows == {}
    assert fixture.repos.artifact_version_repo.rows == {}


@pytest.mark.parametrize(
    "path_alias",
    [
        "./result.txt",
        "result.txt/",
        "result.txt//",
        ".\\result.txt",
    ],
)
def test_hub_ingress_receipt_rejects_noncanonical_path_alias(
    tmp_path: Path,
    path_alias: str,
) -> None:
    fixture = _service_fixture(tmp_path)
    content = b"canonical path evidence"
    (fixture.workspace / "result.txt").write_bytes(content)
    receipt = _materialize(
        fixture,
        _manifest(content),
    )["artifacts"][0]
    receipt["workspace_relative_path"] = path_alias

    verified = RecoveryResultVerificationService._verify_artifact(
        repos=fixture.repos,
        task=fixture.task,
        artifact=receipt,
    )

    assert verified["_exists"] is False
    assert verified["_hash_verified"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_verdict", "passed"),
        ("id", "different-artifact"),
        ("path", "different-result.txt"),
    ],
)
def test_hub_ingress_receipt_rejects_unknown_or_conflicting_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    fixture = _service_fixture(tmp_path)
    content = b"closed receipt evidence"
    (fixture.workspace / "result.txt").write_bytes(content)
    receipt = _materialize(
        fixture,
        _manifest(content),
    )["artifacts"][0]
    receipt[field] = value

    verified = RecoveryResultVerificationService._verify_artifact(
        repos=fixture.repos,
        task=fixture.task,
        artifact=receipt,
    )

    assert verified["_exists"] is False
    assert verified["_hash_verified"] is False


class Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class ReceiptHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        data: dict[str, Any],
        **values: Any,
    ) -> Response:
        self.calls.append(
            {"url": url, "data": data, **values}
        )
        descriptor = data["artifacts"][0]
        receipt = {
            "kind": descriptor["kind"],
            "task_id": data["task_id"],
            "artifact_id": (
                "recovery-artifact-" + "a" * 32
            ),
            "artifact_version_id": (
                "recovery-artifact-version-" + "a" * 32
            ),
            "filename": descriptor["filename"],
            "media_type": descriptor["media_type"],
            "workspace_relative_path": descriptor[
                "relative_path"
            ],
            "content_hash": descriptor["sha256"],
            "size_bytes": descriptor["size_bytes"],
            "provenance_summary": {
                "schema": (
                    "ananta.recovery_artifact_provenance.v1"
                ),
                "authority": "hub",
                "ingress": "workspace",
                "worker_url": data["worker_url"],
                "manifest_digest": data["digest"],
                "source_index": 0,
            },
        }
        return Response(
            {
                "status": "success",
                "data": {
                    "schema": (
                        "ananta.recovery_artifact_receipts.v1"
                    ),
                    "task_id": data["task_id"],
                    "manifest_digest": data["digest"],
                    "artifacts": [receipt],
                    # Replay is aggregate metadata only; it never leaks into
                    # the closed per-artifact receipt.
                    "replayed": True,
                },
            }
        )


def test_worker_accepts_aggregate_replay_without_receipt_field_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.config import settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = b"publisher content"
    (workspace / "result.txt").write_bytes(content)
    client = ReceiptHttpClient()
    monkeypatch.setattr(settings, "agent_name", WORKER_ID)
    monkeypatch.setattr(settings, "agent_url", WORKER_URL)
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    publisher = RecoveryWorkerArtifactPublisher(
        http_client_provider=lambda: client,
        workspace_service_provider=lambda: WorkspaceService(
            workspace
        ),
        token_provider=lambda: WORKER_TOKEN,
    )

    receipts = publisher.publish(
        task={"id": TASK_ID, "worker_execution_context": {}},
        artifacts=[
            {
                "kind": "workspace_file",
                "workspace_relative_path": "result.txt",
                "filename": "result.txt",
                "media_type": "text/plain",
                "content_hash": hashlib.sha256(
                    content
                ).hexdigest(),
                "artifact_id": "worker-local-artifact",
                "artifact_version_id": "worker-local-version",
            }
        ],
        lease_token=LEASE_TOKEN,
        request_fingerprint=REQUEST_FINGERPRINT,
    )

    assert receipts is not None
    assert "_replayed" not in receipts[0]
    assert set(receipts[0]) == _RECEIPT_FIELDS_FOR_TEST
    assert client.calls[0]["data"]["artifacts"][0][
        "workspace_path"
    ] == "result.txt"
    assert client.calls[0]["data"]["artifacts"][0][
        "relative_path"
    ] == "result.txt"


def test_execute_step_replaces_worker_local_refs_with_hub_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.config import settings
    from agent.services import _task_scoped_step_orchestrator, recovery_worker_artifact_publisher

    receipt = {
        "kind": "workspace_file",
        "task_id": TASK_ID,
        "artifact_id": "recovery-artifact-" + "a" * 32,
        "artifact_version_id": (
            "recovery-artifact-version-" + "a" * 32
        ),
        "filename": "result.txt",
        "media_type": "text/plain",
        "workspace_relative_path": "result.txt",
        "content_hash": "a" * 64,
        "size_bytes": 10,
        "provenance_summary": {
            "schema": (
                "ananta.recovery_artifact_provenance.v1"
            ),
            "authority": "hub",
            "ingress": "workspace",
            "worker_url": WORKER_URL,
            "manifest_digest": "a" * 64,
            "source_index": 0,
        },
    }

    class Publisher:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def publish(self, **values: Any) -> list[dict[str, Any]]:
            self.calls.append(values)
            return [dict(receipt)]

    publisher = Publisher()
    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(
        recovery_worker_artifact_publisher,
        "get_recovery_worker_artifact_publisher",
        lambda: publisher,
    )
    outcome = SimpleNamespace(
        data={
            "artifacts": [
                {
                    "kind": "workspace_file",
                    "artifact_id": "worker-local-artifact",
                    "artifact_version_id": "worker-local-version",
                    "workspace_relative_path": "result.txt",
                }
            ]
        }
    )

    first = (
        _task_scoped_step_orchestrator
        ._publish_recovery_artifact_receipts(
            task={"id": TASK_ID},
            outcome=outcome,
            token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
        )
    )
    second = (
        _task_scoped_step_orchestrator
        ._publish_recovery_artifact_receipts(
            task={"id": TASK_ID},
            outcome=first,
            token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
        )
    )

    assert first is outcome
    assert second is outcome
    assert outcome.data["artifacts"] == [receipt]
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["lease_token"] == LEASE_TOKEN
    assert publisher.calls[0]["request_fingerprint"] == (
        REQUEST_FINGERPRINT
    )


def test_execute_step_uses_trusted_local_adapter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.config import settings
    from agent.services import (
        _task_scoped_step_orchestrator,
        recovery_trusted_local_artifact_adapter,
    )

    receipt = _receipt()

    class Adapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def materialize(
            self,
            **values: Any,
        ) -> list[dict[str, Any]]:
            self.calls.append(values)
            return [dict(receipt)]

    adapter = Adapter()
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_can_be_worker", True)
    monkeypatch.setattr(
        recovery_trusted_local_artifact_adapter,
        "get_recovery_trusted_local_artifact_adapter",
        lambda: adapter,
    )
    outcome = SimpleNamespace(
        data={
            "artifacts": [
                {
                    "kind": "workspace_file",
                    "workspace_relative_path": "result-0.txt",
                    "content_hash": "1" * 64,
                }
            ]
        }
    )

    first = (
        _task_scoped_step_orchestrator
        ._publish_recovery_artifact_receipts(
            task={"id": TASK_ID},
            outcome=outcome,
            token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
        )
    )
    second = (
        _task_scoped_step_orchestrator
        ._publish_recovery_artifact_receipts(
            task={"id": TASK_ID},
            outcome=first,
            token=LEASE_TOKEN,
            request_fingerprint=REQUEST_FINGERPRINT,
        )
    )

    assert second is outcome
    assert outcome.data["artifacts"] == [receipt]
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["lease_token"] == LEASE_TOKEN
    assert adapter.calls[0]["request_fingerprint"] == (
        REQUEST_FINGERPRINT
    )


def test_recovery_workspace_sync_returns_claims_without_legacy_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import worker_workspace_service
    from agent.services.worker_workspace_service import (
        WorkerWorkspaceService,
    )

    path = tmp_path / "result.txt"
    content = b"single ingress authority"
    path.write_bytes(content)
    ingestion_calls = 0

    def legacy_ingestion() -> Any:
        nonlocal ingestion_calls
        ingestion_calls += 1
        raise AssertionError("legacy artifact persistence is forbidden")

    monkeypatch.setattr(
        worker_workspace_service,
        "get_ingestion_service",
        legacy_ingestion,
    )
    service = WorkerWorkspaceService()
    task = {
        "id": TASK_ID,
        "derivation_reason": "goal_task_recovery",
        "current_worker_job_id": "recovery-job",
        "status_reason_details": {
            "model_recovery_release": {
                "source_task_id": "source-task"
            }
        },
    }
    sync_cfg = {
        "enabled": True,
        "sync_to_hub": True,
        "max_changed_files": 5,
        "max_file_size_bytes": 1024,
    }

    refs = service.sync_changed_files_to_artifacts(
        task_id=TASK_ID,
        task=task,
        workspace_dir=tmp_path,
        changed_rel_paths=["result.txt"],
        sync_cfg=sync_cfg,
    )
    diff_ref = service.create_workspace_diff_artifact(
        task_id=TASK_ID,
        task=task,
        workspace_dir=tmp_path,
        changed_rel_paths=["result.txt"],
        sync_cfg=sync_cfg,
    )

    assert ingestion_calls == 0
    assert diff_ref is None
    assert len(refs) == 1
    assert refs[0]["workspace_relative_path"] == "result.txt"
    assert refs[0]["content_hash"] == hashlib.sha256(
        content
    ).hexdigest()
    assert "artifact_id" not in refs[0]
    assert "artifact_version_id" not in refs[0]


def test_native_degraded_diagnostics_are_not_pseudo_artifacts() -> None:
    from agent.services.native_worker_runtime_service import (
        NativeWorkerRuntimeService,
    )

    result = NativeWorkerRuntimeService._degraded_execution_outcome(
        tid=TASK_ID,
        trace_id="trace-native-degraded",
        failure_type="runtime_failure",
        degraded={"reason": "delegation_required"},
        policy_classification_summary="blocked",
    )

    assert result["artifact_refs"] == []
    assert result["native_runtime"]["degraded"] == {
        "reason": "delegation_required"
    }
    assert result["approval_decision"]["reason_code"] == (
        "native_worker_in_process_execution_disabled"
    )


def test_recovery_research_report_uses_workspace_claim_and_hub_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.config import settings
    from agent.services import (
        recovery_research_workspace_artifact_service,
        task_execution_tracking_service,
    )
    from agent.services.task_execution_tracking_service import (
        TaskExecutionTrackingService,
    )

    fixture = _service_fixture(tmp_path)
    research_service = RecoveryResearchWorkspaceArtifactService(
        workspace_service_provider=lambda: fixture.workspace_service,
    )
    monkeypatch.setattr(
        recovery_research_workspace_artifact_service,
        "get_recovery_research_workspace_artifact_service",
        lambda: research_service,
    )

    class ForbiddenIngestion:
        upload_calls = 0
        extract_calls = 0

        def upload_artifact(self, **_values: Any) -> Any:
            self.upload_calls += 1
            raise AssertionError("legacy upload must not run")

        def extract_artifact(self, *_args: Any) -> Any:
            self.extract_calls += 1
            raise AssertionError("legacy extraction must not run")

    ingestion = ForbiddenIngestion()
    monkeypatch.setattr(
        task_execution_tracking_service,
        "get_ingestion_service",
        lambda: ingestion,
    )
    report = "# Recovery research\n\nVerified workspace evidence.\n"
    task = dict(vars(fixture.task))
    claim = TaskExecutionTrackingService().persist_research_artifact(
        tid=TASK_ID,
        task=task,
        research_artifact={
            "kind": "research_report",
            "report_markdown": report,
            "sources": [
                {
                    "title": "Source",
                    "url": "https://example.com",
                }
            ],
            "citations": [{"source": "https://example.com"}],
            "trace": {"trace_bundle_id": "trace-research"},
        },
    )

    assert claim is not None
    assert ingestion.upload_calls == 0
    assert ingestion.extract_calls == 0
    assert "artifact_id" not in claim
    assert "artifact_version_id" not in claim
    assert claim["workspace_relative_path"] == (
        "recovery-artifacts/research-report.md"
    )
    assert (
        fixture.workspace / claim["workspace_relative_path"]
    ).read_text(encoding="utf-8") == report

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_can_be_worker", True)
    monkeypatch.setattr(settings, "agent_name", WORKER_ID)
    monkeypatch.setattr(settings, "agent_url", WORKER_URL)
    manifest_service = RecoveryWorkspaceArtifactManifestService(
        workspace_service_provider=lambda: fixture.workspace_service,
    )
    receipts = RecoveryTrustedLocalArtifactAdapter(
        manifest_service_provider=lambda: manifest_service,
        ingress_service_provider=lambda: fixture.service,
    ).materialize(
        task=task,
        artifacts=[claim],
        lease_token=LEASE_TOKEN,
        request_fingerprint=REQUEST_FINGERPRINT,
    )

    assert receipts is not None
    assert receipts[0]["kind"] == "research_report"
    assert receipts[0]["workspace_relative_path"] == (
        "recovery-artifacts/research-report.md"
    )
    assert receipts[0]["artifact_id"].startswith(
        "recovery-artifact-"
    )
    version = fixture.repos.artifact_version_repo.get_by_id(
        receipts[0]["artifact_version_id"]
    )
    assert Path(version.storage_path).read_text(
        encoding="utf-8"
    ) == report


_RECEIPT_FIELDS_FOR_TEST = {
    "kind",
    "task_id",
    "artifact_id",
    "artifact_version_id",
    "filename",
    "media_type",
    "workspace_relative_path",
    "content_hash",
    "size_bytes",
    "provenance_summary",
}
