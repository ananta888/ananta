"""Real production byte ports across isolated containers; synthetic content only."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

CONTENT = b'{"classification":"synthetic_test","answer":42}\n'


class WorkerArtifacts:
    """Execute delegated IO; never delegate work or issue Hub receipts."""

    def __init__(self, engine):
        import os

        from bpmn_container.transport import HUB_URL, WORKER_ID, WORKER_URL

        from agent.db_models import ArtifactDB, ArtifactVersionDB
        from agent.services.artifact_store import ArtifactStore
        from worker.runtime.native_graph.artifact_http import NativeArtifactHttpTransport
        from worker.runtime.native_graph.artifact_inputs import NativeArtifactInputDownloader
        from worker.runtime.native_graph.artifact_publication import (
            NativeArtifactPublisher,
            NativeNodeArtifactPublication,
        )
        from worker.runtime.native_graph.artifact_reader import LocalNativeArtifactReader
        from worker.runtime.native_graph.artifact_repository import LocalArtifactRows
        from worker.runtime.native_graph.artifact_writer import LocalNativeArtifactWriter

        self.storage = ArtifactStore("/tmp/worker-artifacts")
        self.writer = LocalNativeArtifactWriter(engine=engine, artifact_model=ArtifactDB,
                                                version_model=ArtifactVersionDB, storage=self.storage)
        rows = LocalArtifactRows(engine=engine, artifact_model=ArtifactDB, version_model=ArtifactVersionDB)
        transport = NativeArtifactHttpTransport(hub_url=HUB_URL, bearer_token=os.environ["BPMN_WORKER_TOKEN"],
                                                worker_id=WORKER_ID, worker_url=WORKER_URL, timeout_seconds=5)
        self.publication = NativeNodeArtifactPublication(
            reader=LocalNativeArtifactReader(storage_root=self.storage.base_dir, load_artifact=rows.load_artifact,
                                            load_version=rows.load_version),
            publisher=NativeArtifactPublisher(transport=transport),
        )
        self.downloader = NativeArtifactInputDownloader(transport=transport)

    def produce(self, command, task):
        from ananta_contracts.native_context_bundle import native_context_digest

        binding = {"hub_task_id": task["id"], "tenant_id": command.tenant_id, "project_id": task.get("project_id"),
                   "command_digest": native_context_digest(command.to_dict()), "attempt_id": command.attempt_id,
                   "fencing_token": command.fencing_token, "output_name": "report"}
        local = self.writer.store(filename="synthetic-result.json", media_type="application/json",
                                  content=CONTENT, binding=binding)
        refs = self.publication.publish(command=command, task=task, references={"report": local})
        assert refs["report"] != local
        return refs, {"worker_artifact_ref": local, "sha256": hashlib.sha256(CONTENT).hexdigest(),
                      "size_bytes": len(CONTENT), "worker_bytes_verified": True}

    def consume(self, command, task):
        item = self.downloader.download(command=command, hub_task_id=task["id"],
                                        project_id=task.get("project_id"), input_name="report")
        assert item.content == CONTENT
        return {"consumer_bytes_verified": True, "sha256": item.sha256, "size_bytes": len(item.content)}


def prepare_artifact_store():
    from sqlmodel import SQLModel

    import agent.db_models  # noqa: F401 - explicit worker-local test schema
    from agent.database import engine

    SQLModel.metadata.create_all(engine)
    return WorkerArtifacts(engine)


class HubArtifacts:
    """Inject only runtime composition lookup; routes/auth/SQL remain production."""

    def __init__(self, hub):
        from unittest.mock import patch

        from agent.routes.tasks import native_artifact_ingress
        from agent.services.artifact_store import ArtifactStore
        from agent.services.native_artifact_runtime import NativeArtifactRuntime

        self.storage = ArtifactStore("/tmp/hub-artifacts")
        runtime = NativeArtifactRuntime(engine=hub.engine, key_ring=lambda: hub.keys, storage=self.storage)
        self.lookup = patch.object(native_artifact_ingress, "get_native_artifact_runtime", lambda: runtime)
        self.lookup.start()
        hub.app.register_blueprint(native_artifact_ingress.native_artifact_ingress_bp)

    def close(self):
        self.lookup.stop()


def compose_hub_artifacts(hub):
    return HubArtifacts(hub)


def artifact_transfer_case(hub):
    from bpmn_container.acceptance import advance, compile_request, event_summary
    from bpmn_container.fixtures import artifact_handoff
    from sqlmodel import Session, select

    from agent.db_models import ArtifactDB, ArtifactVersionDB

    request = compile_request(artifact_handoff(), "artifact-handoff")
    hub.active_request = request
    result = advance(hub, request, hub.orchestrator.start(request))
    assert result.status == "completed", result.reason_code
    observations = hub.observations(request.run_id)
    assert [row["node"] for row in observations] == ["artifact_work", "artifact_consumer"]
    assert observations[0]["result"]["worker_bytes_verified"]
    assert observations[1]["result"]["consumer_bytes_verified"]
    producer = hub.tasks_for(request.run_id)[0]
    receipt = producer.verification_status["native_result_receipt"]
    assert receipt["classification"] == "technical_observation" and receipt["artifacts"]
    reference = result.checkpoint.state.runtime_metadata["artifact_refs"]["report"]
    assert reference != observations[0]["result"]["worker_artifact_ref"]
    with Session(hub.engine) as session:
        artifact = session.get(ArtifactDB, reference.removeprefix("artifact://"))
        version = session.exec(select(ArtifactVersionDB).where(
            ArtifactVersionDB.id == artifact.latest_version_id)).one()
        assert Path(version.storage_path).read_bytes() == CONTENT
        assert version.sha256 == hashlib.sha256(CONTENT).hexdigest()
        assert artifact.created_by == "hub_native_artifact_ingress"
    return {"status": "completed", "positive_artifact_transfer_verified": True,
            "producer_receipt_classification": receipt["classification"], "tasks": observations,
            "events": event_summary(hub, request), "hub_bytes_verified": True,
            "production_release_evidence": False}


def artifact_case(hub):
    """Unadmitted references cannot terminalize a genuinely executed Task."""
    import copy

    from bpmn_container.acceptance import compile_request
    from bpmn_container.fixtures import artifact

    request = compile_request(artifact(), "artifact-unadmitted")
    hub.active_request = request
    hub.orchestrator.start(request)
    for _ in range(10):
        if hub.tasks_for(request.run_id):
            break
        hub.orchestrator.advance(request)
    hub.dispatch_ready(request.run_id)
    injected = False

    def tamper(response):
        nonlocal injected
        response = copy.deepcopy(response)
        verification = response["workflow_adapter_verification"]
        nested = verification["workflow_adapter_task_result"]["adapter_result"]["verification"]["native_node_result"]
        admitted = nested["artifact_refs"]["report"]
        local = nested["output_data"]["worker_artifact_ref"]

        def replace_reference(value):
            if isinstance(value, dict):
                return {key: replace_reference(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_reference(item) for item in value]
            return local if value == admitted else value

        injected = True
        return replace_reference(response)

    deadline = time.monotonic() + 15
    rejection = None
    while hub.pending and time.monotonic() < deadline:
        try:
            hub.collect(transform_result=tamper)
        except ValueError as exc:
            rejection = str(exc)
        time.sleep(0.025)
    task, = hub.tasks_for(request.run_id)
    assert injected and rejection == "native_artifact_hub_receipt_required" and not hub.pending, rejection
    assert task.status == "in_progress" and not task.verification_status.get("native_result_receipt")
    owner = hub.ownership.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id="artifact_work")
    assert not owner.result_ack_key
    return {"status": "rejected_before_task_result_persistence", "reason_code": rejection,
            "canonical_step_completed": False, "ownership_acknowledged": False}
