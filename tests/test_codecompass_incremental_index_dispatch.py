from __future__ import annotations

import hashlib
import json

import pytest

from agent.services.codecompass_layer_service import (
    LAYER_RESULT_SCHEMA,
    CodeCompassLayerDispatchBackend,
)


class _QueryBackend:
    def list_profiles(self): return ["default"]
    def show_head(self, profile_id): return None
    def diff(self, **kwargs): return dict(kwargs)
    def plan_update(self, **kwargs): return dict(kwargs["plan"])
    def apply_update(self, **kwargs): raise AssertionError("worker execution reached the hub")
    def compact(self, **kwargs):
        return {"new_manifest": {"revision": "rev-2", "artifacts": {"symbols": {}}}}


class _Queue:
    def __init__(self): self.envelopes = []
    def dispatch(self, *, envelope):
        self.envelopes.append(dict(envelope))
        return {"task_id": envelope["task_id"], "assignment_id": "assign-1", "dispatch_lease_id": "lease-1"}


class _Repository:
    def __init__(self): self.records = {}
    def get(self, task_id): return self.records.get(task_id)
    def save(self, record): self.records[str(record["task_id"])] = dict(record)


class _Publisher:
    def __init__(self): self.calls = []
    def publish(self, *, dispatch, result):
        self.calls.append((dispatch, result))
        return {"status": "published", "generation": 2}


def _backend():
    queue = _Queue()
    repository = _Repository()
    publisher = _Publisher()
    backend = CodeCompassLayerDispatchBackend(
        query_backend=_QueryBackend(),
        task_queue=queue,
        dispatch_repository=repository,
        publisher=publisher,
        writes_enabled=True,
    )
    return backend, queue, repository, publisher


def _plan():
    return {"new_manifest": {"revision": "rev-2", "artifacts": {"symbols": {}, "relations": {}}}}


def test_hub_does_not_execute_worker_builder_directly() -> None:
    backend, queue, _repository, publisher = _backend()
    result = backend.apply_update(
        plan=_plan(),
        profile_ref={"name": "default", "digest": "profile-v1"},
        profile_id="default",
        expected_generation=1,
        idempotency_key="update-rev-2",
    )
    assert result["status"] == "queued"
    assert len(queue.envelopes) == 1
    assert publisher.calls == []


def test_bound_result_required_before_publish() -> None:
    backend, _queue, repository, publisher = _backend()
    dispatched = backend.apply_update(
        plan=_plan(),
        profile_ref={"name": "default", "digest": "profile-v1"},
        profile_id="default",
        expected_generation=1,
        idempotency_key="update-rev-2",
    )
    record = repository.get(dispatched["task_id"])
    intent = record["intent"]
    artifacts = {
        "relations": {"content_digest": "b" * 64},
        "symbols": {"content_digest": "a" * 64},
    }
    digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    result = {
        "schema": LAYER_RESULT_SCHEMA,
        "status": "completed",
        "task_id": dispatched["task_id"],
        "assignment_id": "assign-1",
        "dispatch_lease_id": "lease-1",
        "intent_digest": intent["intent_digest"],
        "input_revision": intent["input_revision"],
        "profile_digest": intent["profile_digest"],
        "artifact_kinds": ["relations", "symbols"],
        "artifact_set": artifacts,
        "artifact_set_digest": digest,
    }
    with pytest.raises(ValueError, match="binding_invalid"):
        backend.admit_result({**result, "dispatch_lease_id": "foreign-lease"})
    assert publisher.calls == []
    assert backend.admit_result(result)["status"] == "published"
    assert len(publisher.calls) == 1


def test_layer_writes_are_default_off() -> None:
    backend = CodeCompassLayerDispatchBackend(
        query_backend=_QueryBackend(),
        task_queue=_Queue(),
        dispatch_repository=_Repository(),
        publisher=_Publisher(),
    )
    with pytest.raises(RuntimeError, match="writes_disabled"):
        backend.apply_update(
            plan=_plan(), profile_ref={}, profile_id="default", expected_generation=1, idempotency_key="x"
        )
