from agent.services.task_orchestration_service import TaskOrchestrationDependencies, TaskOrchestrationService


class _TaskRecord:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class _TaskRepo:
    def get_all(self):
        return [_TaskRecord({"id": "T-1", "status": "todo"})]


class _PolicyDecisionRepo:
    def get_all(self, limit=50):
        return []


class _RepositoryRegistry:
    task_repo = _TaskRepo()
    policy_decision_repo = _PolicyDecisionRepo()


class _ExecutionTrackingService:
    def build_execution_reconciliation_snapshot(self):
        return {"stale_worker_jobs": []}


def test_task_orchestration_service_accepts_explicit_dependencies():
    service = TaskOrchestrationService(
        TaskOrchestrationDependencies(
            get_task_status=lambda task_id: {"id": task_id},
            update_task_status=lambda *args, **kwargs: None,
            forward_task_to_worker=lambda *args, **kwargs: {"status": "success", "data": {"ok": True}},
            repository_registry=lambda: _RepositoryRegistry(),
            routing_advisor=lambda: None,
            context_policy_service=lambda: None,
            execution_tracking_service=lambda: _ExecutionTrackingService(),
        )
    )

    model = service.orchestration_read_model()

    assert model["queue"]["todo"] == 1
    assert model["worker_execution_reconciliation"] == {"stale_worker_jobs": []}
