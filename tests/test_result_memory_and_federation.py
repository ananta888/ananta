from types import SimpleNamespace

from agent.db_models import MemoryEntryDB
from agent.services.remote_federation_policy_service import get_remote_federation_policy_service
from agent.services.result_memory_service import ResultMemoryService, normalize_result_memory_policy
from agent.services.task_neighborhood_service import TaskNeighborhoodService


class _MemoryRepo:
    def __init__(self):
        self.saved = []
        self.by_task = {}

    def save(self, entry):
        self.saved.append(entry)
        self.by_task.setdefault(entry.task_id, []).append(entry)
        return entry

    def get_by_task(self, task_id):
        return list(self.by_task.get(task_id, []))

    def get_by_goal(self, _goal_id):
        return []


class _TaskRepo:
    def __init__(self, tasks):
        self.tasks = {task.id: task for task in tasks}

    def get_by_id(self, task_id):
        return self.tasks.get(task_id)

    def get_all(self):
        return list(self.tasks.values())


def test_result_memory_creates_compact_followup_artifact_metadata(monkeypatch):
    repo = _MemoryRepo()
    monkeypatch.setattr("agent.services.result_memory_service.memory_entry_repo", repo)
    service = ResultMemoryService()

    entry = service.record_worker_result_memory(
        task_id="T-1",
        goal_id="G-1",
        trace_id="TRACE-1",
        worker_job_id="W-1",
        title="Implement routing",
        output="Changed agent/services/foo.py\nTests passed\nNext step: review docs",
        retrieval_tags=["coding", "completed"],
        policy={"retrieval_document_max_chars": 600, "archive_raw_output": True},
    )

    metadata = entry.memory_metadata
    assert metadata["memory_format"] == "worker_result_compact_v3"
    assert metadata["followup_artifact"]["kind"] == "task_result_summary"
    assert metadata["structured_summary"]["changed_files"] == ["agent/services/foo.py"]
    assert metadata["raw_history"].startswith("Changed")
    assert len(metadata["retrieval_document"]) <= 600


def test_result_memory_policy_normalizes_bounds():
    policy = normalize_result_memory_policy(
        {"retrieval_document_max_chars": 10, "raw_history_max_chars": 999999, "archive_raw_output": True}
    )

    assert policy["retrieval_document_max_chars"] == 400
    assert policy["raw_history_max_chars"] == 100000
    assert policy["archive_raw_output"] is True


def test_task_neighborhood_uses_goal_dependency_and_file_overlap():
    memory_repo = _MemoryRepo()
    memory_repo.by_task["T-1"] = [
        MemoryEntryDB(task_id="T-1", memory_metadata={"structured_summary": {"changed_files": ["agent/services/foo.py"]}})
    ]
    memory_repo.by_task["T-2"] = [
        MemoryEntryDB(task_id="T-2", memory_metadata={"structured_summary": {"changed_files": ["agent/routes/foo.py"]}})
    ]
    task_repo = _TaskRepo(
        [
            SimpleNamespace(id="T-1", goal_id="G-1", plan_id="P-1", depends_on=["T-3"]),
            SimpleNamespace(id="T-2", goal_id="G-1", plan_id="P-2", depends_on=[]),
            SimpleNamespace(id="T-3", goal_id="G-2", plan_id="P-3", depends_on=[]),
        ]
    )

    neighborhood = TaskNeighborhoodService(task_repository=task_repo, memory_entry_repository=memory_repo).build_neighborhood("T-1")

    assert neighborhood["reason"] == "ok"
    assert "T-2" in neighborhood["neighbor_task_ids"]
    assert "T-3" in neighborhood["neighbor_task_ids"]
    t2 = next(item for item in neighborhood["neighbors"] if item["task_id"] == "T-2")
    assert "same_goal" in t2["reasons"]
    assert "file_symbol_overlap" in t2["reasons"]


def test_remote_federation_policy_blocks_artifacts_by_default_and_adds_provenance_headers():
    service = get_remote_federation_policy_service()
    backend_policy = service.normalize_backend({"id": "remote-a"}, cfg={})

    blocked = service.evaluate(
        backend_policy=backend_policy,
        operation="artifact",
        hop_count=1,
        provenance={"trace_id": "TRACE"},
    )
    allowed_chat = service.evaluate(
        backend_policy=backend_policy,
        operation="chat",
        hop_count=1,
        provenance={"trace_id": "TRACE"},
    )
    headers = service.provenance_headers(local_instance_id="hub-a", trace_id="TRACE", hop_count=1)

    assert blocked.allowed is False
    assert blocked.reason == "remote_operation_not_allowed"
    assert allowed_chat.allowed is True
    assert headers["X-Ananta-Hop-Count"] == "2"
    assert headers["X-Ananta-Instance-ID"] == "hub-a"


def test_remote_federation_policy_enforces_hops_and_provenance():
    service = get_remote_federation_policy_service()
    backend_policy = service.normalize_backend({"allowed_operations": ["chat"], "max_hops": 2}, cfg={})

    no_provenance = service.evaluate(backend_policy=backend_policy, operation="chat", hop_count=1, provenance={})
    too_many_hops = service.evaluate(
        backend_policy=backend_policy,
        operation="chat",
        hop_count=3,
        provenance={"trace_id": "TRACE"},
    )

    assert no_provenance.reason == "remote_provenance_required"
    assert too_many_hops.reason == "remote_max_hops_exceeded"
