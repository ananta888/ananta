from agent.services.planning_dataset_export_service import _redact, PlanningDatasetExportService


class _Run:
    def __init__(self):
        self.id = "r1"
        self.goal_id = "g1"
        self.trace_id = "t1"
        self.mode = "generic"
        self.mode_data = {"token": "abc", "safe": "x"}
        self.model_provider = "lmstudio"
        self.model_name = "test123"
        self.planning_profile = "small_local"
        self.prompt_version_id = "v1"
        self.parse_mode = "strict_json"
        self.parse_confidence = "high"
        self.parse_warnings = []
        self.repair_needed = False
        self.repair_success = True
        self.repair_attempt_count = 0
        self.validation_success = True
        self.validation_errors = []
        self.generated_task_count = 2
        self.status = "planned"
        self.created_at = 1.0
        self.raw_output_preview = "secret=abc"
        self.raw_output_ref = "artifact://x"


class _Repo:
    def get_recent(self, limit=200):
        return [_Run()]


class _Registry:
    planning_run_repo = _Repo()


def test_redact_secrets():
    red = _redact({"api_key": "x", "nested": {"token": "y"}, "safe": "ok"})
    assert red["api_key"] == "***REDACTED***"
    assert red["nested"]["token"] == "***REDACTED***"
    assert red["safe"] == "ok"


def test_jsonl_export_valid(monkeypatch):
    from agent.services import planning_dataset_export_service as mod

    monkeypatch.setattr(mod, "get_repository_registry", lambda: _Registry())
    svc = PlanningDatasetExportService()
    out = svc.export(output_format="jsonl", include_raw_output=True)
    assert out["format"] == "jsonl"
    assert out["count"] == 1
    assert '"model_name": "test123"' in out["jsonl"]
