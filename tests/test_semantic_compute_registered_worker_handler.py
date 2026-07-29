from __future__ import annotations

import io
import time

from flask import Flask
from PIL import Image

from agent.ai_agent import _register_worker_domain_handlers
from agent.services.task_handler_registry import get_task_handler_registry
from ananta_contracts.semantic_compute import SemanticComputeWorkerTask
from worker.semantic_media.compute_task_handler import (
    BoundedSemanticExecutor,
    RegisteredSemanticComputeTaskHandler,
)
from worker.semantic_media.handler import SemanticComputeWorkerHandler


class _HubClient:
    def __init__(self, *, revoke_after: int | None = None) -> None:
        image = Image.new("RGB", (8, 4), (10, 20, 30))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        self.content = stream.getvalue()
        self.revoke_after = revoke_after
        self.authorize_calls = 0
        self.published = []
        self.submitted = []

    def authorized(self, _task) -> bool:
        self.authorize_calls += 1
        return self.revoke_after is None or self.authorize_calls <= self.revoke_after

    def read_input(self, _task, _reference):
        return self.content, "image/png"

    def publish(self, _task, content):
        self.published.append(content)
        return "artifact:result-a"

    def submit_result(self, result):
        self.submitted.append(dict(result))


def _task() -> SemanticComputeWorkerTask:
    return SemanticComputeWorkerTask(
        task_id="semantic-compute-handler-a",
        parent_task_id="parent-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        lease_id="lease-a",
        fencing_token=1,
        session_id="session-a",
        epoch=1,
        task_type="visual_extract",
        audience="owner-a",
        input_refs=("artifact:input-a",),
        deadline_epoch_ms=int(time.time() * 1000) + 30_000,
        resource_budget={
            "cpu_ms": 5_000,
            "memory_bytes": 1_048_576,
            "artifact_bytes": 64_000,
        },
        artifact_publish_ref="artifact-publish:result-a",
    )


def _handler(client: _HubClient) -> RegisteredSemanticComputeTaskHandler:
    runtime = SemanticComputeWorkerHandler(
        executor=BoundedSemanticExecutor(client),  # type: ignore[arg-type]
        publisher=client,  # type: ignore[arg-type]
        lease_guard=client,  # type: ignore[arg-type]
    )
    return RegisteredSemanticComputeTaskHandler(runtime, client)  # type: ignore[arg-type]


def test_registered_handler_executes_one_envelope_and_submits_fenced_result() -> None:
    client = _HubClient()
    task = _task()
    result = _handler(client).execute(task={"worker_execution_context": {"semantic_compute": task.to_dict()}})
    assert result["status"] == "completed"
    assert len(client.published) == 1 and len(client.submitted) == 1
    assert client.submitted[0]["lease_id"] == "lease-a"
    assert client.submitted[0]["artifact_refs"] == ["artifact:result-a"]


def test_registered_handler_stops_before_input_or_publish_after_lease_loss() -> None:
    client = _HubClient(revoke_after=1)
    task = _task()
    result = _handler(client).execute(task={"worker_execution_context": {"semantic_compute": task.to_dict()}})
    assert result["status"] == "failed"
    assert result["reason_code"] == "execution_authority_lost"
    assert client.published == [] and client.submitted == []


def test_worker_opt_in_registers_handler_and_advertises_hub_authorized_capabilities(monkeypatch) -> None:
    import agent.adapters.vector_store_metrics_adapter as metrics_module
    import agent.ai_agent as app_module
    import worker.retrieval.knowledge_index_job_handler as knowledge_module
    import worker.retrieval.vector_index_execution as vector_execution_module
    import worker.retrieval.vector_index_job_handler as vector_handler_module
    import worker.retrieval.vector_index_task_verification as verification_module
    import worker.semantic_media.compute_task_handler as semantic_module

    class _NoopHandler:
        def propose(self, **_kwargs):
            return {}

        def execute(self, **_kwargs):
            return {}

    monkeypatch.setattr(app_module.settings, "role", "worker")
    monkeypatch.setenv("ANANTA_SEMANTIC_COMPUTE_WORKER_ENABLED", "true")
    monkeypatch.setattr(knowledge_module, "build_knowledge_index_task_handler", _NoopHandler)
    monkeypatch.setattr(semantic_module, "build_semantic_compute_task_handler", _NoopHandler)
    captured: dict[str, object] = {}

    class _Observer:
        pass

    class _VectorExecution:
        def __init__(self, *, observer=None):
            captured["observer"] = observer

    class _Verifier:
        def verify(self, _envelope):
            return None

    def build_vector_handler(execution, *, task_verifier):
        captured["execution"] = execution
        captured["task_verifier"] = task_verifier
        return _NoopHandler()

    monkeypatch.setattr(metrics_module, "PrometheusVectorStoreObserver", _Observer)
    monkeypatch.setattr(
        vector_execution_module,
        "ConfiguredVectorIndexExecution",
        _VectorExecution,
    )
    monkeypatch.setattr(
        vector_handler_module,
        "build_vector_index_task_handler",
        build_vector_handler,
    )
    monkeypatch.setattr(
        verification_module,
        "load_vector_index_task_verifier",
        lambda: _Verifier(),
    )
    app = Flask(__name__)
    app.extensions["workflow_adapter_worker_registration"] = {
        "capabilities": ["workflow_execute"],
        "runtime_targets": [],
    }

    _register_worker_domain_handlers(app)

    descriptor = get_task_handler_registry(app).resolve_descriptor("semantic_compute")
    assert descriptor is not None
    assert descriptor["safety_flags"]["hub_delegation_required"] is True
    required_capabilities = {
        "semantic_compute",
        "semantic_compute.visual_extract",
        "semantic_compute.visual_validate",
        "semantic_compute.speech_features",
        "semantic_compute.speech_validate",
    }
    assert required_capabilities.issubset(set(descriptor["capabilities"]))
    assert required_capabilities.issubset(set(app.extensions["workflow_adapter_worker_registration"]["capabilities"]))
    assert app.extensions["semantic_compute_worker_registration"]["ready"] is True
    assert isinstance(captured["observer"], _Observer)
    assert captured["execution"].__class__ is _VectorExecution
    assert captured["task_verifier"].__class__ is _Verifier
    assert not hasattr(captured["task_verifier"], "attest")
    vector_descriptor = get_task_handler_registry(app).resolve_descriptor(
        "vector_index_operation"
    )
    assert vector_descriptor is not None
    assert "hub_attestation" in vector_descriptor["verification_hooks"]
    assert (
        "hub_dispatch_admission"
        in vector_descriptor["verification_hooks"]
    )
    assert {
        "retrieval",
        "index_write",
        "vector_index_operation",
    }.issubset(
        app.extensions[
            "workflow_adapter_worker_registration"
        ]["capabilities"]
    )
    assert app.extensions["vector_index_worker_registration"] == {
        "ready": True,
        "reason_code": None,
    }


def test_worker_does_not_advertise_vector_mutation_without_keyring(
    monkeypatch,
) -> None:
    import agent.ai_agent as app_module
    import worker.retrieval.knowledge_index_job_handler as knowledge_module
    import worker.retrieval.vector_index_job_handler as vector_handler_module
    import worker.retrieval.vector_index_task_verification as verification_module

    class _NoopHandler:
        def propose(self, **_kwargs):
            return {}

        def execute(self, **_kwargs):
            return {}

    monkeypatch.setattr(app_module.settings, "role", "worker")
    monkeypatch.delenv(
        "ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE",
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_module,
        "build_knowledge_index_task_handler",
        _NoopHandler,
    )
    monkeypatch.setattr(
        vector_handler_module,
        "build_vector_index_task_handler",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "unverified vector handler must not be built"
            )
        ),
    )
    monkeypatch.setattr(
        verification_module,
        "load_vector_index_task_verifier",
        lambda: verification_module.UnavailableVectorIndexTaskVerifier(),
    )
    app = Flask(__name__)
    app.extensions["workflow_adapter_worker_registration"] = {
        "capabilities": ["workflow_execute"],
        "runtime_targets": [],
    }

    _register_worker_domain_handlers(app)

    assert (
        get_task_handler_registry(app).resolve_descriptor(
            "vector_index_operation"
        )
        is None
    )
    assert "vector_index_operation" not in app.extensions[
        "workflow_adapter_worker_registration"
    ]["capabilities"]
    assert app.extensions["vector_index_worker_registration"] == {
        "ready": False,
        "reason_code": (
            "vector_index_task_verification_keyring_required"
        ),
    }
