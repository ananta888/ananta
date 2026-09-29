from agent.services.planning_review_queue_service import PlanningReviewQueueService


class _Run:
    def __init__(self, run_id, parse_mode="parse_failed"):
        self.id = run_id
        self.parse_mode = parse_mode
        self.model_provider = "lmstudio"
        self.model_name = "test123"
        self.generated_task_count = 0
        self.status = "planned"


class _ReviewRepo:
    def __init__(self):
        self.items = []

    def save(self, item):
        self.items.append(item)
        return item

    def get_open(self, limit=200):
        return self.items[:limit]


class _RunRepo:
    def get_recent(self, limit=50):
        return [_Run("a"), _Run("b"), _Run("c")]


class _Registry:
    planning_run_repo = _RunRepo()
    planning_review_item_repo = _ReviewRepo()


def test_repeated_parse_failures_create_review_item(monkeypatch):
    from agent.services import planning_review_queue_service as mod

    reg = _Registry()
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    svc = PlanningReviewQueueService()
    created = svc.evaluate_run_for_review(_Run("d"))
    assert created
    assert str(created[0].review_type) == "repeated_parse_failed"
