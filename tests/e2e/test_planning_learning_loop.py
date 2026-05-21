from __future__ import annotations

from types import SimpleNamespace

from agent.db_models import PlanningPromptVersionDB
from agent.routes.config import shared
from agent.services.config_read_model_service import ConfigReadModelService
from agent.services.planning_learning_loop_service import PlanningLearningLoopService


class _EmptyRepo:
    def get_all(self):
        return []


class _RunRepo:
    def __init__(self, runs):
        self.runs = list(runs)

    def get_recent(self, limit=100):
        return list(self.runs)[:limit]


class _ProfileRepo:
    def __init__(self, profiles):
        self.profiles = list(profiles)
        self.saved = []

    def get_enabled(self):
        return list(self.profiles)

    def save(self, profile):
        self.saved.append(profile)
        return profile


class _PromptVersionRepo:
    def __init__(self, versions):
        self.versions = {str(item.id): item for item in versions}
        self.saved = []

    def get_by_id(self, version_id):
        return self.versions.get(str(version_id))

    def save(self, version):
        self.versions[str(version.id)] = version
        self.saved.append(version)
        return version


class _CandidateRepo:
    def __init__(self):
        self.items = []

    def get_recent(self, limit=100):
        return list(self.items)[:limit]

    def save(self, item):
        if item not in self.items:
            self.items.insert(0, item)
        return item


class _ReviewRepo:
    def get_open(self, limit=200):
        return []


class _Registry:
    def __init__(self, runs, profiles, versions):
        self.team_repo = _EmptyRepo()
        self.role_repo = _EmptyRepo()
        self.template_repo = _EmptyRepo()
        self.agent_repo = _EmptyRepo()
        self.task_repo = _EmptyRepo()
        self.planning_run_repo = _RunRepo(runs)
        self.planning_model_profile_repo = _ProfileRepo(profiles)
        self.planning_prompt_version_repo = _PromptVersionRepo(versions)
        self.planning_template_candidate_repo = _CandidateRepo()
        self.planning_review_item_repo = _ReviewRepo()


def _make_run(idx: int, *, profile_name: str, prompt_version_id: str):
    return SimpleNamespace(
        id=f"run-{idx}",
        planning_profile=profile_name,
        mode="new_software_project",
        mode_data={"__intent__": {"goal_type": "software_project"}},
        prompt_version_id=prompt_version_id,
        parse_mode="parse_failed",
        repair_needed=True,
        validation_success=False,
        generated_task_count=0,
        repair_attempt_count=3,
        parse_warnings=["truncate"],
    )


def test_planning_learning_loop_surface_flow(monkeypatch):
    import agent.services.config_read_model_service as crm
    import agent.services.planning_learning_loop_service as pls

    active_version = PlanningPromptVersionDB(
        version="v1",
        language="de",
        mode="new_software_project",
        output_contract={},
        system_rules=[],
        user_prompt_template="base",
        repair_prompt_template="repair",
        checksum="chk-1",
        enabled=True,
    )
    runs = [_make_run(idx, profile_name="lmstudio_laptop", prompt_version_id=str(active_version.id)) for idx in range(1, 9)]
    reg = _Registry(
        runs=runs,
        profiles=[
            SimpleNamespace(
                profile_name="lmstudio_laptop",
                provider="lmstudio",
                model_name_pattern="gemma-4e4b",
                model_family="gemma",
                preferred_prompt_version_id=str(active_version.id),
                enabled=True,
            )
        ],
        versions=[active_version],
    )
    monkeypatch.setattr(pls, "get_repository_registry", lambda: reg)
    monkeypatch.setattr(crm, "get_repository_registry", lambda: reg)
    monkeypatch.setattr(
        crm,
        "get_integration_registry_service",
        lambda: SimpleNamespace(list_execution_backends=lambda include_preflight=True: {"preflight": {"providers": {}, "cli_backends": {}}}),
    )
    monkeypatch.setattr(
        pls,
        "get_planning_metrics_service",
        lambda: SimpleNamespace(
            summarize=lambda **_: {
                "run_count": 8,
                "groups": [
                    {
                        "group": "lmstudio::gemma::lmstudio_laptop",
                        "model_key": "lmstudio::gemma",
                        "run_count": 8,
                        "parse_success_rate": 0.0,
                        "repair_rate": 1.0,
                        "validation_success_rate": 0.0,
                        "materialization_success_rate": 0.0,
                        "quality_score": 0.0,
                        "trend_direction": "degrading",
                        "sample_size_is_small": False,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        pls,
        "get_planning_prompt_evolver_service",
        lambda: SimpleNamespace(
            evolve_from_run=lambda **_: {
                "evolved": True,
                "new_prompt_version_id": "new-version-id",
                "new_prompt_version": "v1.evo.1",
                "profile_updated": False,
                "activated_profile": False,
            }
        ),
    )
    monkeypatch.setattr(
        pls,
        "get_model_response_behavior_aggregation_service",
        lambda: SimpleNamespace(aggregate=lambda **_: {"observed_run_count": 8, "primary_output_shape_distribution": {"parse_failed": 1.0}}),
    )
    monkeypatch.setattr(pls, "get_planning_review_queue_service", lambda: SimpleNamespace(evaluate_run_for_review=lambda run: []))

    run_result = PlanningLearningLoopService().run_once(
        planning_policy={
            "learning_loop": {
                "enabled": True,
                "min_runs": 5,
                "min_failures": 1,
                "min_parse_success_rate": 0.9,
                "min_validation_success_rate": 0.9,
                "min_materialization_success_rate": 0.9,
                "max_repair_rate": 0.2,
                "candidate_activation_threshold": 0.95,
                "rollback_threshold": 0.6,
                "freeze_minutes": 1,
                "canary_window_runs": 3,
                "auto_activate": False,
                "require_review_before_activate": False,
            }
        }
    )

    assert run_result["candidates_created"] == 1

    dashboard = ConfigReadModelService().dashboard_read_model(
        cfg={"planning_policy": {"learning_loop": {"enabled": True, "lookback_runs": 20, "freeze_minutes": 10}}},
        benchmark_task_kind="analysis",
        benchmark_task_kinds={"analysis"},
        include_task_snapshot=False,
        benchmark_rows_builder=lambda **_: ([], {"task_kind": "analysis"}),
        benchmark_recommendation_builder=lambda **_: {"recommended": None},
        system_health_builder=lambda: {"checks": []},
        contract_catalog_builder=lambda: {"version": "v1", "schemas": {}},
        hub_copilot_summary_builder=lambda cfg: {},
        context_policy_summary_builder=lambda cfg: {},
        artifact_flow_summary_builder=lambda cfg: {},
        planning_learning_summary_builder=shared.planning_learning_settings_summary,
    )

    learning = ((dashboard.get("llm_configuration") or {}).get("planning_learning") or {})
    snapshot = learning.get("snapshot") or {}
    assert learning.get("requested") is not None
    assert snapshot.get("enabled") is True
    assert snapshot.get("candidate_count") == 1
    assert (snapshot.get("profiles") or [])[0]["current_candidate"]["status"] == "proposed"
