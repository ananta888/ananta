from types import SimpleNamespace

from agent.services import planning_template_mining_service as svc


class _RunRepo:
    def __init__(self, runs):
        self._runs = runs

    def get_recent(self, limit=200):
        return self._runs[:limit]


class _EvalRepo:
    def __init__(self, eval_by_run):
        self._eval_by_run = eval_by_run

    def get_by_run_id(self, rid):
        return self._eval_by_run.get(rid)


class _Sink:
    def __init__(self):
        self.items = []

    def save(self, item):
        self.items.append(item)
        return item


class _Registry:
    def __init__(self, runs, eval_by_run):
        self.planning_run_repo = _RunRepo(runs)
        self.planning_evaluation_repo = _EvalRepo(eval_by_run)
        self.planning_template_candidate_repo = _Sink()
        self.planning_pattern_cluster_repo = _Sink()


def test_mining_creates_candidates_and_clusters(monkeypatch):
    runs = [
        SimpleNamespace(
            id="r1",
            mode="new_software_project",
            mode_data={"__intent__": {"goal_type": "software_project"}},
            parse_mode="strict_json",
            repair_strategy_used="llm_config",
            dependency_mode_distribution={"parallel": 2},
            generated_task_count=3,
            prompt_version_id="p1",
            planning_profile="small_local",
        )
    ]
    eval_by_run = {"r1": SimpleNamespace(total_score=0.9)}
    reg = _Registry(runs, eval_by_run)
    monkeypatch.setattr(svc, "get_repository_registry", lambda: reg)

    out = svc.get_planning_template_mining_service().mine_candidates(min_total_score=0.8, limit=50)
    assert out["created_candidates"] == 1
    assert out["created_clusters"] == 1
