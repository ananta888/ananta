"""Hub adapters behind ``CodeCompassLayerDispatchBackend``'s queue and publisher ports.

* ``HubTaskQueueLayerDispatcher`` reserves the run in the Hub Evidence
  Registry, stores the full job spec in the Hub's dispatch record and queues
  a task that carries only the ``JobTicket``.
* ``HubCodeCompassLayerPublisher`` accepts a layer only if the Worker
  uploaded exactly the promised one: same address, snapshot, parent and
  kind; then it advances the head under the dispatch's expected generation
  and completes the run.

Workers never touch heads: they build one blob and the Hub decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.codecompass_layer_job import (
    CONTEXT_KEY,
    JOB_SPEC_SCHEMA,
    REQUIRED_CAPABILITIES,
    TASK_KIND,
    JobTicket,
    canonical_digest,
    partition_by_size,
)

PUBLICATION_SCHEMA = "ananta.codecompass_layer_publication.v1"
BUILDER_ENVIRONMENT = {"builder": "worker.incremental_index.chunk_builder", "version": 1}


class LayerRunEvidencePort(Protocol):
    def reserve(self, *, envelope: Mapping[str, Any], assignment_id: str, dispatch_lease_id: str) -> str: ...

    def complete(self, *, run_id: str, assignment_id: str, dispatch_lease_id: str, succeeded: bool,
                 result_digest: str) -> None: ...


class TaskIngressPort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class HubEvidenceLayerRuns:
    """``RUN_*`` for every layer build, bound to an admitted ``SRC_*`` of its snapshot."""

    def __init__(self, registry: Any, *, tenant_id: str, project_id: str) -> None:
        self._registry = registry
        self._tenant = tenant_id
        self._project = project_id

    def reserve(self, *, envelope: Mapping[str, Any], assignment_id: str, dispatch_lease_id: str) -> str:
        revision = str(envelope["input_revision"])
        source = self._registry.register_source(
            tenant_id=self._tenant,
            project_id=self._project,
            origin_type="codecompass_layer_snapshot",
            origin_digest=revision,
            content_digest=revision,
            policy_digest=str(envelope["profile_digest"]),
            evidence_scope="local",
        )
        run = self._registry.reserve_run(
            tenant_id=self._tenant,
            project_id=self._project,
            task_id=str(envelope["task_id"]),
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            repository_revision=revision,
            input_digest=str(envelope["intent_digest"]),
            execution_profile_digest=str(envelope["profile_digest"]),
            environment_digest=canonical_digest(BUILDER_ENVIRONMENT),
            source_ids=[source.source_id],
            evidence_scope="local",
            idempotency_key=str(envelope["task_id"]),
        )
        return str(run.run_id)

    def complete(self, *, run_id: str, assignment_id: str, dispatch_lease_id: str, succeeded: bool,
                 result_digest: str) -> None:
        self._registry.record_result(
            tenant_id=self._tenant,
            project_id=self._project,
            run_id=run_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            terminal_state="succeeded" if succeeded else "failed",
            result_digest=result_digest,
        )


class HubTaskQueueLayerDispatcher:
    """``CodeCompassLayerTaskQueuePort``: one Hub task per layer build."""

    def __init__(self, *, queue: TaskIngressPort, evidence: LayerRunEvidencePort) -> None:
        self._queue = queue
        self._evidence = evidence

    def dispatch(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = str(envelope["task_id"])
        assignment_id = f"cc_assign_{str(envelope['intent_digest'])[:32]}"
        dispatch_lease_id = f"hub-task:{task_id}"
        run_id = self._evidence.reserve(envelope=envelope, assignment_id=assignment_id,
                                        dispatch_lease_id=dispatch_lease_id)
        ticket = JobTicket(task_id=task_id, assignment_id=assignment_id, dispatch_lease_id=dispatch_lease_id,
                           intent_digest=str(envelope["intent_digest"]), run_id=run_id)
        plan = dict(envelope.get("plan") or {})
        self._queue.ingest_task(
            task_id=task_id,
            status="todo",
            title=f"CodeCompass layer: {envelope.get('profile_id')} @ {str(envelope['input_revision'])[:12]}",
            description="Hub-delegated CodeCompass chunks layer build",
            priority="medium",
            created_by="codecompass-layers",
            source="codecompass_layers",
            tags=["codecompass_layers", "hub_delegated"],
            event_channel="hub_task_queue",
            event_details={
                "profile_id": str(envelope.get("profile_id") or ""),
                "decision": str((plan.get("decision") or {}).get("decision_type") or ""),
                "file_changes": len(list((plan.get("changeset") or {}).get("file_changes") or [])),
            },
            extra_fields={
                "task_kind": TASK_KIND,
                "required_capabilities": list(REQUIRED_CAPABILITIES),
                "worker_execution_context": {CONTEXT_KEY: ticket.to_dict()},
            },
        )
        return {"task_id": task_id, "assignment_id": assignment_id, "dispatch_lease_id": dispatch_lease_id,
                "run_id": run_id, "content_parts": self._content_parts(plan)}

    @staticmethod
    def _content_parts(plan: Mapping[str, Any]) -> list[list[str]]:
        """Content parts fixed at dispatch time, sized by the manifest's byte sizes."""
        sizes: dict[str, int] = {}
        for change in list((plan.get("changeset") or {}).get("file_changes") or []):
            digest = str(change.get("new_content_sha256") or "")
            if digest:
                sizes[digest] = max(1, int(change.get("new_byte_size") or 1))
        return partition_by_size([(digest, sizes.get(digest, 1)) for digest in list(plan.get("content_sha256") or [])])


def job_spec(dispatch: Mapping[str, Any]) -> dict[str, Any]:
    """What a Worker needs to build the layer, projected from the Hub's dispatch record."""
    intent = dict(dispatch.get("intent") or {})
    binding = dict(dispatch.get("binding") or {})
    plan = dict(intent.get("plan") or {})
    changeset = dict(plan.get("changeset") or {})
    return {
        "schema": JOB_SPEC_SCHEMA,
        "task_id": str(dispatch.get("task_id") or ""),
        "profile_id": str(intent.get("profile_id") or ""),
        "input_revision": str(intent.get("input_revision") or ""),
        "profile_digest": str(intent.get("profile_digest") or ""),
        "intent_digest": str(intent.get("intent_digest") or ""),
        "assignment_id": str(binding.get("assignment_id") or ""),
        "dispatch_lease_id": str(binding.get("dispatch_lease_id") or ""),
        "run_id": str(binding.get("run_id") or ""),
        "artifact_kinds": list(intent.get("artifact_kinds") or []),
        "parent_layer_id": plan.get("parent_layer_id"),
        "changeset_id": str(changeset.get("changeset_id") or ""),
        "file_changes": list(changeset.get("file_changes") or []),
        "prior_record_ids_by_path": dict(plan.get("prior_record_ids_by_path") or {}),
        "compatibility_key": dict(plan.get("compatibility_key") or {}),
        "content_parts": len(list(binding.get("content_parts") or [])),
        "expected_generation": int(intent.get("expected_generation") or 0),
    }


class HubCodeCompassLayerPublisher:
    """``CodeCompassLayerPublisherPort``: verify the uploaded layer and advance the head."""

    def __init__(self, *, layers: Any, heads: Any, evidence: LayerRunEvidencePort,
                 observers: list[Any] | None = None) -> None:
        self._layers = layers
        self._heads = heads
        self._evidence = evidence
        self._observers = list(observers or [])

    def publish(self, *, dispatch: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = job_spec(dispatch)
        try:
            publication = self._publish(spec, result)
        except Exception:
            self._complete(spec, result, succeeded=False)
            raise
        self._complete(spec, result, succeeded=True)
        from agent.services.codecompass_layer_publication_observers import notify

        notify(self._observers, publication)
        return publication

    def reject(self, *, dispatch: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        """A bound Worker failure: the reserved run ends as failed, the head stays."""
        self._complete(job_spec(dispatch), result, succeeded=False)

    def _publish(self, spec: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        artifact = dict((result.get("artifact_set") or {}).get("chunks") or {})
        layer_id = str(artifact.get("content_digest") or "")
        layer = self._layers.get_layer(layer_id) if self._layers.has_layer(layer_id) else None
        if layer is None:
            raise ValueError("codecompass_layer_not_uploaded")
        if str(layer.get("snapshot_revision") or "") != spec["input_revision"]:
            raise ValueError("codecompass_layer_snapshot_mismatch")
        if (layer.get("parent_layer_id") or None) != (spec["parent_layer_id"] or None):
            raise ValueError("codecompass_layer_parent_mismatch")
        if str(layer.get("artifact_kind") or "") != "chunks":
            raise ValueError("codecompass_layer_kind_mismatch")
        profile_id = spec["profile_id"]
        head = self._heads.get_head(profile_id)
        if head is None:
            outcome = self._heads.create_head(profile_id, layer_id=layer_id, layer_set={"chunks": layer_id},
                                              snapshot_revision=spec["input_revision"], reason="base")
        else:
            outcome = self._heads.update_head(
                profile_id,
                expected_generation=spec["expected_generation"],
                new_layer_id=layer_id,
                new_layer_set={"chunks": layer_id},
                snapshot_revision=spec["input_revision"],
                append_delta=bool(spec["parent_layer_id"]),
                replace_artifact_kinds=None if spec["parent_layer_id"] else ["chunks"],
                reason="delta" if spec["parent_layer_id"] else "base",
            )
        if not outcome.success:
            raise ValueError(f"codecompass_layer_head_{outcome.error}")
        return {
            "schema": PUBLICATION_SCHEMA,
            "status": "published",
            "task_id": spec["task_id"],
            "profile_id": profile_id,
            "layer_id": layer_id,
            "generation": int(outcome.new_generation),
            "snapshot_revision": spec["input_revision"],
            "run_id": spec["run_id"],
        }

    def _complete(self, spec: Mapping[str, Any], result: Mapping[str, Any], *, succeeded: bool) -> None:
        if spec["run_id"]:
            self._evidence.complete(run_id=spec["run_id"], assignment_id=spec["assignment_id"],
                                    dispatch_lease_id=spec["dispatch_lease_id"], succeeded=succeeded,
                                    result_digest=canonical_digest(dict(result)))

