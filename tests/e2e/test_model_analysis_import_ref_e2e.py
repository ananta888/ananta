from __future__ import annotations

import json

from agent.repositories.model_analysis_job_repository import (
    SQLiteModelAnalysisJobRepository,
)
from agent.services.model_analysis_artifact_publisher import (
    ModelAnalysisArtifactPublisher,
)
from agent.services.model_analysis_job_service import ModelAnalysisJobService
from agent.services.model_analysis_report_composer import (
    ModelAnalysisReportComposer,
)
from agent.services.model_analysis_task_port import ModelAnalysisTaskReference
from agent.services.model_intelligence_artifact_store import (
    FileSystemModelIntelligenceArtifactStore,
)
from ananta_contracts.model_intelligence import AnalysisJob
from ananta_contracts.model_intelligence_execution import CompletionOutcome
from worker.model_intelligence.executor import (
    BoundedWorkerResourcePool,
    InMemoryCancellationRegistry,
    ModelAnalysisWorkerExecutor,
)
from worker.model_intelligence.handlers import (
    AdmittedSnapshot,
    ModelAnalysisHandlerError,
    TenantBoundAdmittedSnapshotResolver,
    build_static_analysis_handlers,
)

MODEL_ID = f"model_{'a' * 64}"
IMPORT_REF = "snapshot:fixture-001"


class _HubTasks:
    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []

    def submit(self, job):
        self.events.append(("submit", job.job_id))
        return ModelAnalysisTaskReference(
            job.hub_task_id,
            f"execution-{job.job_id}",
            "assigned",
        )

    def mark_running(self, job, *, worker_id):
        self.events.append(("running", job.job_id, worker_id))

    def mark_cancel_requested(self, job, *, reason_code):
        self.events.append(("cancel", job.job_id, reason_code))

    def finish(self, job, *, status, reason_code):
        self.events.append(("finish", job.job_id, status))


def _write_snapshot(root) -> None:
    header = {
        "model.layers.0.weight": {
            "data_offsets": [0, 8],
            "dtype": "F32",
            "shape": [1, 2],
        }
    }
    encoded_header = json.dumps(
        header,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (root / "model.safetensors").write_bytes(
        len(encoded_header).to_bytes(8, "little")
        + encoded_header
        + b"\0" * 8
    )
    (root / "tokenizer.json").write_text(
        json.dumps(
            {
                "model": {"vocab": {"hello": 0, "world": 1}},
                "normalizer": {"type": "NFC"},
                "pre_tokenizer": {"type": "Whitespace"},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "bos_token": "<s>",
                "chat_template": "{{ messages }}",
                "model_max_length": 2048,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "config.json").write_text(
        json.dumps(
            {
                "quantization_config": {
                    "bits": 4,
                    "group_size": 128,
                    "quant_method": "gptq",
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _job(index: int, analysis_kind: str, artifact_kind: str) -> AnalysisJob:
    return AnalysisJob(
        job_id=f"job-{index:03d}",
        hub_task_id=f"task-{index:03d}",
        tenant_id="tenant-001",
        model_id=MODEL_ID,
        analysis_kind=analysis_kind,
        profile_id="profile.static-safe.v1",
        request_sha256=f"{index:064x}",
        requested_artifact_kinds=(artifact_kind,),
        max_runtime_seconds=60,
        max_output_bytes=128 * 1024,
        extensions={"x-import-ref": IMPORT_REF},
    )


def test_import_ref_submit_execute_complete_artifact_and_report_roundtrip(
    tmp_path,
) -> None:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    _write_snapshot(snapshot_root)
    resolver = TenantBoundAdmittedSnapshotResolver(
        (
            AdmittedSnapshot(
                tenant_id="tenant-001",
                import_ref=IMPORT_REF,
                snapshot_root=snapshot_root,
                weight_files=("model.safetensors",),
                model_id=MODEL_ID,
            ),
        )
    )
    store = FileSystemModelIntelligenceArtifactStore(
        root=tmp_path / "artifacts"
    )
    publisher = ModelAnalysisArtifactPublisher(store)
    executor = ModelAnalysisWorkerExecutor(
        handlers=build_static_analysis_handlers(
            resolver=resolver,
            publisher=publisher,
        ),
        resources=BoundedWorkerResourcePool(
            max_active=1,
            max_memory_bytes=16 * 1024 * 1024,
        ),
        cancellations=InMemoryCancellationRegistry(),
        epoch_ms=lambda: 2_000,
    )
    tasks = _HubTasks()
    jobs = ModelAnalysisJobService(
        repository=SQLiteModelAnalysisJobRepository(
            tmp_path / "model-analysis.sqlite3"
        ),
        tasks=tasks,
        epoch_ms=lambda: 1_000,
    )
    specifications = (
        ("static.tensor-statistics", "tensor.statistics"),
        ("static.tokenizer", "tokenizer.analysis"),
        ("static.quantization", "quantization.analysis"),
    )
    artifacts = []
    for index, (analysis_kind, artifact_kind) in enumerate(
        specifications,
        start=1,
    ):
        job = _job(index, analysis_kind, artifact_kind)
        queued = jobs.submit(
            job,
            idempotency_key=f"idempotency-{index:03d}",
        )
        _running, lease = jobs.claim(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            worker_id="worker-001",
            expected_version=queued.version,
            lease_seconds=60,
            max_memory_bytes=16 * 1024 * 1024,
        )
        completion = executor.execute(job, lease)
        assert completion.outcome is CompletionOutcome.SUCCEEDED
        completed = jobs.complete(completion)
        assert completed.completion == completion
        artifact = completion.artifacts[0]
        assert publisher.load_json(
            tenant_id=job.tenant_id,
            reference=artifact,
        )["status"] in {"available", "not_available"}
        artifacts.append(artifact)

    composer = ModelAnalysisReportComposer(
        artifact_store=store,
        publisher=publisher,
    )
    report = composer.compose(
        tenant_id="tenant-001",
        report_job_id="job-report-001",
        model_identity={"model_id": MODEL_ID},
        artifacts=artifacts,
    )
    replay = composer.compose(
        tenant_id="tenant-001",
        report_job_id="job-report-001",
        model_identity={"model_id": MODEL_ID},
        artifacts=artifacts,
    )
    loaded = composer.load(
        tenant_id="tenant-001",
        reference=report.json_ref,
    )

    assert replay == report
    assert report.content_digest == f"sha256:{report.json_ref.sha256}"
    assert [section["name"] for section in loaded["sections"]] == [
        "quantization",
        "static",
        "tokenizer",
    ]
    assert [event[0] for event in tasks.events].count("submit") == 3
    assert [event[0] for event in tasks.events].count("finish") == 3

    try:
        resolver.resolve(
            tenant_id="tenant-002",
            import_ref=IMPORT_REF,
        )
    except ModelAnalysisHandlerError as exc:
        assert exc.reason_code == "admitted_snapshot_tenant_mismatch"
    else:
        raise AssertionError("cross-tenant snapshot resolution must fail")
