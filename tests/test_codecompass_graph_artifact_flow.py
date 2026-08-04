from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agent.services.codecompass_graph_artifact_resolver import (
    CodeCompassGraphArtifactResolver,
)
from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.knowledge_index_worker_artifact_service import (
    KnowledgeIndexWorkerArtifactService,
)
from worker.retrieval.codecompass_graph_artifact_materializer import (
    WorkerCodeCompassGraphArtifactMaterializer,
    normalize_graph_visual_options,
)
from worker.retrieval.knowledge_index_job_handler import (
    build_knowledge_index_task_handler,
)


class _Model:
    def __init__(self, **payload):
        self.payload = dict(payload)

    def model_dump(self):
        return dict(self.payload)


class _Task(_Model):
    pass


class _Repository:
    def __init__(self):
        self.items = {}

    def get_by_id(self, item_id):
        return self.items.get(item_id)

    def save(self, item):
        self.items[item.id] = item
        return item


class _Queue:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)
        fields = dict(kwargs.get("extra_fields") or {})
        self.repository.items[kwargs["task_id"]] = _Task(
            id=kwargs["task_id"],
            status=kwargs["status"],
            created_at=10.0,
            updated_at=10.0,
            verification_status={},
            **fields,
        )


def _write_worker_outputs(root: Path, *, volatile_manifest_value: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps({"generated_at": volatile_manifest_value, "index_record_count": 2}),
        encoding="utf-8",
    )
    (root / "index.jsonl").write_text(
        '{"file":"a.py","content":"a"}\n{"file":"b.py","content":"b"}\n',
        encoding="utf-8",
    )
    (root / "graph_nodes.jsonl").write_text(
        "\n".join(
            (
                '{"id":"node:a","kind":"python_file","file":"a.py","line_count":10}',
                '{"id":"node:b","kind":"python_function","file":"b.py","line_count":4}',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "graph_edges.jsonl").write_text(
        '{"source":"node:a","target":"node:b","type":"contains_symbol","confidence":0}\n',
        encoding="utf-8",
    )


def test_worker_graph_artifacts_are_deterministic_and_path_free(tmp_path) -> None:
    first_root = tmp_path / "worker-a" / "outputs"
    second_root = tmp_path / "worker-b" / "outputs"
    _write_worker_outputs(first_root, volatile_manifest_value="first-container-time")
    _write_worker_outputs(second_root, volatile_manifest_value="second-container-time")
    options = {
        "include_advanced_metrics": True,
        "blast_radius_seeds": ["node:a"],
    }
    materializer = WorkerCodeCompassGraphArtifactMaterializer()

    first = materializer.materialize(
        knowledge_index={"id": "idx-1", "source_scope": "repo_path"},
        run={"id": "run-1", "output_dir": str(first_root), "profile_name": "default"},
        options=options,
    )
    second = materializer.materialize(
        knowledge_index={"id": "idx-1", "source_scope": "repo_path"},
        run={"id": "run-1", "output_dir": str(second_root), "profile_name": "default"},
        options=options,
    )

    assert first["graph_revision"] == second["graph_revision"]
    for filename in ("cc_graph_index.json", "cc_graph_index.visual_metrics.json"):
        first_content = (first_root / filename).read_bytes()
        second_content = (second_root / filename).read_bytes()
        assert first_content == second_content
        assert str(first_root).encode() not in first_content
        assert str(second_root).encode() not in second_content
    metrics = json.loads((first_root / "cc_graph_index.visual_metrics.json").read_text())
    assert metrics["metric_capabilities"]["degree_centrality"]["status"] == "available"
    assert metrics["metric_capabilities"]["bridge_score"]["status"] == "approximate"
    assert metrics["metric_capabilities"]["blast_radius"]["status"] == "approximate"


def test_worker_materializer_enforces_hub_graph_size_before_publication(
    tmp_path,
) -> None:
    output = tmp_path / "bounded-output"
    _write_worker_outputs(output, volatile_manifest_value="worker")

    with pytest.raises(RuntimeError, match="graph_artifact_too_large"):
        WorkerCodeCompassGraphArtifactMaterializer(
            max_graph_artifact_bytes=256,
        ).materialize(
            knowledge_index={"id": "idx-bounded", "source_scope": "repo_path"},
            run={"id": "run-bounded", "output_dir": str(output)},
        )

    assert not (output / "cc_graph_index.json").exists()


def test_worker_graph_artifact_preserves_semantic_nodes_and_edges(tmp_path) -> None:
    output = tmp_path / "semantic-output"
    _write_worker_outputs(output, volatile_manifest_value="worker")
    (output / "semantic_nodes.jsonl").write_text(
        "\n".join(
            (
                '{"id":"semantic:type:A","kind":"data_record","file":"a.py"}',
                '{"id":"semantic:method:A.run","kind":"function_signature","file":"a.py"}',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "semantic_edges.jsonl").write_text(
        '{"source":"semantic:type:A","target":"semantic:method:A.run","edge_type":"declares"}\n',
        encoding="utf-8",
    )
    semantic_budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": True,
        "truncated_node_count": 1,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 2,
        "semantic_node_bytes": (output / "semantic_nodes.jsonl").stat().st_size,
        "semantic_edge_bytes": (output / "semantic_edges.jsonl").stat().st_size,
        "candidate_edge_record_limit": 20000,
        "candidate_edge_byte_limit": 16777216,
        "candidate_edge_count": 4,
        "candidate_edge_bytes": 384,
        "truncated_candidate_edge_count": 0,
    }
    (output / "manifest.json").write_text(
        json.dumps({"semantic_budget": semantic_budget}),
        encoding="utf-8",
    )

    WorkerCodeCompassGraphArtifactMaterializer().materialize(
        knowledge_index={"id": "idx-semantic", "source_scope": "repo_path"},
        run={"id": "run-semantic", "output_dir": str(output)},
    )

    graph = json.loads((output / "cc_graph_index.json").read_text(encoding="utf-8"))
    assert [node["id"] for node in graph["semantic_nodes"]] == [
        "semantic:method:A.run",
        "semantic:type:A",
    ]
    assert len(graph["semantic_edges"]) == 1
    assert graph["semantic_edges"][0]["source_id"] == "semantic:type:A"
    semantic_diagnostics = graph["diagnostics"]["semantic_translation"]
    assert semantic_diagnostics["status"] == "degraded"
    assert semantic_diagnostics["reason"] == "semantic_graph_partial"
    assert semantic_diagnostics["semantic_budget"] == semantic_budget


@pytest.mark.parametrize(
    "invalid_row",
    ("{broken-json}\n", "[]\n"),
)
def test_worker_graph_materializer_rejects_invalid_graph_rows(
    tmp_path,
    invalid_row,
) -> None:
    output = tmp_path / "invalid-graph-output"
    _write_worker_outputs(output, volatile_manifest_value="worker")
    with (output / "graph_edges.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(invalid_row)

    with pytest.raises(RuntimeError, match="knowledge_index_graph_output_invalid"):
        WorkerCodeCompassGraphArtifactMaterializer().materialize(
            knowledge_index={"id": "idx-invalid", "source_scope": "repo_path"},
            run={"id": "run-invalid", "output_dir": str(output)},
        )

    assert not (output / "cc_graph_index.json").exists()


def test_graph_visual_options_are_strict_and_input_bounded() -> None:
    assert normalize_graph_visual_options(None)["include_advanced_metrics"] is True
    with pytest.raises(ValueError, match="fields_unknown"):
        normalize_graph_visual_options({"run_shell": True})
    with pytest.raises(ValueError, match="blast_seeds_invalid"):
        normalize_graph_visual_options({"blast_radius_seeds": ["node"] * 257})
    with pytest.raises(ValueError, match="advanced_metrics_invalid"):
        normalize_graph_visual_options({"include_advanced_metrics": "yes"})


def test_graph_capable_profile_rejects_empty_graph_output(tmp_path) -> None:
    output = tmp_path / "empty-graph"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps({"index_record_count": 1}), encoding="utf-8"
    )
    (output / "index.jsonl").write_text(
        '{"id":"src/example.py","content":"def example(): pass"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="knowledge_index_graph_output_empty"):
        WorkerCodeCompassGraphArtifactMaterializer().materialize(
            knowledge_index={"id": "idx-empty", "source_scope": "repo_path"},
            run={
                "id": "run-empty",
                "output_dir": str(output),
                "run_metadata": {
                    "profile": {"limits": {"graph_export_mode": "jsonl"}}
                },
            },
        )


def test_legacy_custom_artifact_publisher_remains_additively_compatible() -> None:
    class IndexService:
        def index_source_records(self, **_kwargs):
            return (
                _Model(id="idx-legacy", status="completed"),
                _Model(id="run-legacy", knowledge_index_id="idx-legacy", status="completed"),
            )

    class LegacyPublisher:
        def publish(self, *, job_id, knowledge_index, run):
            return [
                {
                    "artifact_id": f"legacy-{role}",
                    "sha256": "a" * 64,
                    "media_type": media_type,
                    "role": role,
                    "filename": filename,
                    "size_bytes": 0,
                    "knowledge_index_id": knowledge_index["id"],
                    "run_id": run["id"],
                }
                for role, filename, media_type in (
                    ("manifest", "manifest.json", "application/json"),
                    ("index", "index.jsonl", "application/x-ndjson"),
                )
            ]

    fingerprint = "b" * 64
    result = build_knowledge_index_task_handler(
        IndexService(),
        artifact_publisher=LegacyPublisher(),
    ).execute(
        {
            "schema": "ananta.knowledge_index_job.v1",
            "job_id": "knowledge-index-" + fingerprint[:32],
            "job_type": "source_records",
            "scope_id": "legacy",
            "source_scope": "repo_path",
            "profile_name": "default",
            "created_by": "test",
            "created_at": 1.0,
            "idempotency_fingerprint": fingerprint,
            "record_count": 1,
            "artifact_ids": [],
            "payload": {
                "source_scope": "repo_path",
                "source_id": "legacy",
                "records": [{"kind": "document"}],
            },
        }
    )

    assert result["status"] == "completed"
    assert {item["role"] for item in result["artifact_refs"]} == {"manifest", "index"}


def test_worker_result_schema_requires_graph_binding_only_for_graph_roles() -> None:
    schema = json.loads(
        Path("schemas/worker/knowledge_index_job_result.v1.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    base_reference = {
        "artifact_id": "artifact-1",
        "sha256": "a" * 64,
        "media_type": "application/json",
        "role": "manifest",
        "filename": "manifest.json",
        "size_bytes": 0,
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
    }
    base_result = {
        "schema": "ananta.knowledge_index_job_result.v1",
        "job_id": "knowledge-index-" + "a" * 32,
        "idempotency_fingerprint": "a" * 64,
        "status": "completed",
        "reason_code": None,
        "knowledge_index": {"id": "idx-1"},
        "run": {"id": "run-1"},
        "results": None,
        "artifact_refs": [base_reference],
        "error": None,
    }
    graph_without_binding = {
        **base_reference,
        "role": "graph_index",
        "filename": "cc_graph_index.json",
    }
    non_graph_with_binding = {
        **base_reference,
        "artifact_schema": "codecompass_graph_index.v1",
        "graph_revision": "sha256:" + "b" * 64,
        "graph_content_hash": "sha256:" + "c" * 64,
    }
    graph_with_wrong_schema = {
        **graph_without_binding,
        "artifact_schema": "graph_visual_metrics.v1",
        "graph_revision": "sha256:" + "b" * 64,
        "graph_content_hash": "sha256:" + "c" * 64,
    }

    assert list(validator.iter_errors(base_result)) == []
    assert list(validator.iter_errors({**base_result, "artifact_refs": [graph_without_binding]}))
    assert list(validator.iter_errors({**base_result, "artifact_refs": [non_graph_with_binding]}))
    assert list(validator.iter_errors({**base_result, "artifact_refs": [graph_with_wrong_schema]}))


def test_hub_download_verifier_preserves_legitimate_empty_artifacts() -> None:
    KnowledgeIndexWorkerArtifactService._verify_downloaded_content(
        reference={"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
        content=b"",
    )


def test_hub_rejects_tampered_visual_metrics_even_with_matching_transport_hash(tmp_path) -> None:
    worker_output = tmp_path / "worker-output"
    _write_worker_outputs(worker_output, volatile_manifest_value="worker")
    WorkerCodeCompassGraphArtifactMaterializer().materialize(
        knowledge_index={"id": "idx-1", "source_scope": "artifact"},
        run={"id": "run-1", "output_dir": str(worker_output)},
    )
    graph_content = (worker_output / "cc_graph_index.json").read_bytes()
    metrics_payload = json.loads(
        (worker_output / "cc_graph_index.visual_metrics.json").read_text(encoding="utf-8")
    )
    metrics_payload["metadata"]["node_count"] = 999
    tampered_metrics = json.dumps(
        metrics_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = (worker_output / "manifest.json").read_bytes()
    index = (worker_output / "index.jsonl").read_bytes()
    content_by_id = {
        "manifest": manifest,
        "index": index,
        "graph": graph_content,
        "metrics": tampered_metrics,
    }

    class Downloader:
        def download(self, *, worker_url, worker_token, reference):
            return content_by_id[reference["artifact_id"]]

    graph_revision = str((json.loads(graph_content)["state"])["manifest_hash"])

    def ref(role: str, filename: str, artifact_id: str, media_type: str) -> dict:
        content = content_by_id[artifact_id]
        result = {
            "artifact_id": artifact_id,
            "sha256": hashlib.sha256(content).hexdigest(),
            "media_type": media_type,
            "role": role,
            "filename": filename,
            "size_bytes": len(content),
            "knowledge_index_id": "idx-1",
            "run_id": "run-1",
        }
        if role == "graph_index":
            result.update(
                artifact_schema="codecompass_graph_index.v1",
                graph_revision=graph_revision,
                graph_content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            )
        elif role == "graph_visual_metrics":
            result.update(
                artifact_schema="graph_visual_metrics.v1",
                graph_revision=graph_revision,
                graph_content_hash=str(metrics_payload["content_hash"]),
            )
        return result

    service = KnowledgeIndexWorkerArtifactService(
        downloader=Downloader(),
        knowledge_index_repository=_Repository(),
        knowledge_index_run_repository=_Repository(),
        output_root=tmp_path / "hub-output",
    )
    task = {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "artifact",
                "source_scope": None,
            }
        },
    }
    result = {
        "status": "completed",
        "knowledge_index": {"id": "idx-1", "source_scope": "artifact", "status": "completed"},
        "run": {"id": "run-1", "knowledge_index_id": "idx-1", "status": "completed"},
        "results": None,
        "artifact_refs": [
            ref("manifest", "manifest.json", "manifest", "application/json"),
            ref("index", "index.jsonl", "index", "application/x-ndjson"),
            ref(
                "graph_index",
                "cc_graph_index.json",
                "graph",
                "application/vnd.ananta.codecompass-graph-index+json",
            ),
            ref(
                "graph_visual_metrics",
                "cc_graph_index.visual_metrics.json",
                "metrics",
                "application/vnd.ananta.codecompass-graph-visual-metrics+json",
            ),
        ],
    }

    with pytest.raises(ValueError, match="visual_metrics_hash_mismatch"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=result,
            task=task,
        )


def test_hub_worker_graph_artifact_flow_crosses_only_artifact_port(
    client,
    auth_header,
    monkeypatch,
    tmp_path,
) -> None:
    worker_root = tmp_path / "worker-container"
    hub_root = tmp_path / "hub-container"
    worker_output = worker_root / "knowledge" / "idx-1" / "run-1"
    _write_worker_outputs(worker_output, volatile_manifest_value="worker-only")

    task_repository = _Repository()
    queue = _Queue(task_repository)
    index_repository = _Repository()
    run_repository = _Repository()
    published_content: dict[str, bytes] = {}
    published_artifacts = []

    class Ingestion:
        def upload_artifact(self, *, filename, content, created_by, media_type):
            artifact_id = f"worker-artifact-{len(published_content) + 1}"
            artifact = SimpleNamespace(id=artifact_id, artifact_metadata={})
            version = SimpleNamespace(
                sha256=hashlib.sha256(content).hexdigest(),
                media_type=media_type,
                size_bytes=len(content),
            )
            published_content[artifact_id] = bytes(content)
            return artifact, version, None

    class ArtifactPortDownloader:
        def download(self, *, worker_url, worker_token, reference):
            assert worker_url == "http://worker-a:5000"
            assert worker_token == "worker-token"
            return published_content[reference["artifact_id"]]

    monkeypatch.setattr(
        "agent.services.ingestion_service.get_ingestion_service",
        lambda: Ingestion(),
    )
    monkeypatch.setattr(
        "agent.repository.artifact_repo",
        SimpleNamespace(save=lambda artifact: published_artifacts.append(artifact) or artifact),
    )

    artifact_admission = KnowledgeIndexWorkerArtifactService(
        downloader=ArtifactPortDownloader(),
        knowledge_index_repository=index_repository,
        knowledge_index_run_repository=run_repository,
        output_root=hub_root / "knowledge_indices",
    )
    hub = KnowledgeIndexJobService(
        task_queue=queue,
        task_repository=task_repository,
        worker_artifact_service=artifact_admission,
        clock=lambda: 10.0,
    )
    queued = hub.submit_source_records_job(
        source_scope="repo_path",
        source_id="ananta",
        records=[{"kind": "document", "content": "delegated"}],
        created_by="admin",
        profile_name="default",
        graph_visual_metrics={
            "include_advanced_metrics": True,
            "blast_radius_seeds": ["node:a"],
        },
    )
    assert queue.calls[0]["extra_fields"]["task_kind"] == "codecompass_index_build"
    task = task_repository.items[queued["job_id"]].payload
    task["assigned_agent_url"] = "http://worker-a:5000"
    task["assigned_agent_token"] = "worker-token"
    envelope = task["worker_execution_context"]["knowledge_index_job"]

    class IndexService:
        def index_source_records(self, **_kwargs):
            return (
                _Model(
                    id="idx-1",
                    source_scope="repo_path",
                    profile_name="default",
                    status="completed",
                    output_dir=str(worker_output),
                    index_metadata={},
                ),
                _Model(
                    id="run-1",
                    knowledge_index_id="idx-1",
                    profile_name="default",
                    status="completed",
                    output_dir=str(worker_output),
                    run_metadata={},
                ),
            )

    worker_handler = build_knowledge_index_task_handler(IndexService())
    worker_result = worker_handler.execute(envelope)
    repeated_worker_result = worker_handler.execute(envelope)
    result_schema = json.loads(
        Path("schemas/worker/knowledge_index_job_result.v1.json").read_text(encoding="utf-8")
    )
    assert list(Draft202012Validator(result_schema).iter_errors(worker_result)) == []
    assert {item["role"] for item in worker_result["artifact_refs"]}.issuperset(
        {"manifest", "index", "graph_index", "graph_visual_metrics"}
    )
    first_graph_artifact_bytes = {
        item["role"]: published_content[item["artifact_id"]]
        for item in worker_result["artifact_refs"]
        if item["role"] in {"graph_index", "graph_visual_metrics"}
    }
    repeated_graph_artifact_bytes = {
        item["role"]: published_content[item["artifact_id"]]
        for item in repeated_worker_result["artifact_refs"]
        if item["role"] in {"graph_index", "graph_visual_metrics"}
    }
    assert repeated_graph_artifact_bytes == first_graph_artifact_bytes
    visual_metrics_schema = json.loads(
        Path("schemas/artifacts/graph_visual_metrics.v1.json").read_text(encoding="utf-8")
    )
    delegated_visual_metrics = json.loads(first_graph_artifact_bytes["graph_visual_metrics"])
    assert list(
        Draft202012Validator(visual_metrics_schema).iter_errors(delegated_visual_metrics)
    ) == []
    graph_references = {
        item["role"]: item
        for item in worker_result["artifact_refs"]
        if item["role"] in {"graph_index", "graph_visual_metrics"}
    }
    assert (
        graph_references["graph_index"]["graph_revision"]
        == graph_references["graph_visual_metrics"]["graph_revision"]
    )

    admitted = hub.materialize_worker_result(
        job_id=queued["job_id"],
        result=worker_result,
        task=task,
    )
    hub_output = hub_root / "knowledge_indices" / "repo_path" / "idx-1" / "run-1"
    assert admitted["knowledge_index"]["output_dir"] == str(hub_output)
    binding = admitted["knowledge_index"]["index_metadata"]["graph_artifacts"]
    assert binding["graph_revision"] == graph_references["graph_index"]["graph_revision"]
    assert binding["graph_index"]["local_path"] == str(hub_output / "cc_graph_index.json")
    assert (hub_output / "cc_graph_index.json").is_file()
    assert (hub_output / "cc_graph_index.visual_metrics.json").is_file()

    # Prove the GET path is independent from the worker filesystem and performs
    # no degree/centrality/blast computation after admission.
    shutil.rmtree(worker_root)
    monkeypatch.setattr(
        "agent.routes.codecompass_graph._knowledge_index_repo",
        lambda: index_repository,
    )
    monkeypatch.setattr(
        "agent.routes.codecompass_graph.get_codecompass_graph_artifact_resolver",
        lambda: CodeCompassGraphArtifactResolver(
            artifact_root=hub_output,
            allow_legacy=False,
        ),
    )
    monkeypatch.setattr(
        "worker.retrieval.codecompass_graph_metrics.compute_graph_metrics",
        lambda **_kwargs: pytest.fail("request path recomputed graph metrics"),
    )
    monkeypatch.setattr(
        "worker.retrieval.codecompass_blast_radius.compute_blast_radius",
        lambda **_kwargs: pytest.fail("request path recomputed blast radius"),
    )
    response = client.get(
        "/api/codecompass/graph?knowledge_index_id=idx-1",
        headers=auth_header,
    )

    assert response.status_code == 200
    graph = response.json["data"]
    assert graph["metadata"]["graph_revision"] == binding["graph_revision"]
    assert graph["metric_capabilities"]["degree_centrality"]["status"] == "available"
    assert graph["metric_capabilities"]["blast_radius"]["status"] == "approximate"
    edge = graph["edges"][0]
    assert edge["attributes"]["confidence"] == 0
