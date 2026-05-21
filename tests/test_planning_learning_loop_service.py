from __future__ import annotations

from types import SimpleNamespace

from agent.db_models import PlanningPromptVersionDB, PlanningTemplateCandidateDB
from agent.services.planning_learning_loop_service import PlanningLearningLoopService


def _make_run(**kwargs):
    base = {
        "id": "run-1",
        "planning_profile": "lmstudio_laptop",
        "mode": "new_software_project",
        "mode_data": {"__intent__": {"goal_type": "software_project"}},
        "prompt_version_id": None,
        "parse_mode": "parse_failed",
        "repair_needed": True,
        "validation_success": False,
        "generated_task_count": 0,
        "repair_attempt_count": 3,
        "parse_warnings": ["truncate"],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


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
    def __init__(self, items=None):
        self.items = list(items or [])

    def get_recent(self, limit=100):
        return list(self.items)[:limit]

    def save(self, item):
        if item not in self.items:
            self.items.insert(0, item)
        return item


class _ReviewRepo:
    def __init__(self, items=None):
        self.items = list(items or [])

    def get_open(self, limit=200):
        return list(self.items)[:limit]


class _Registry:
    def __init__(self, runs, profiles, versions, candidates=None, reviews=None):
        self.planning_run_repo = _RunRepo(runs)
        self.planning_model_profile_repo = _ProfileRepo(profiles)
        self.planning_prompt_version_repo = _PromptVersionRepo(versions)
        self.planning_template_candidate_repo = _CandidateRepo(candidates)
        self.planning_review_item_repo = _ReviewRepo(reviews)


def test_learning_loop_disabled_is_noop(monkeypatch):
    import agent.services.planning_learning_loop_service as mod

    reg = _Registry(
        runs=[_make_run()],
        profiles=[SimpleNamespace(profile_name="lmstudio_laptop", provider="lmstudio", model_name_pattern="gemma", model_family="gemma", preferred_prompt_version_id=None, enabled=True)],
        versions=[],
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)

    result = PlanningLearningLoopService().run_once(planning_policy={"learning_loop": {"enabled": False}})

    assert result["ran"] is False
    assert "disabled" in result["reason_codes"]


def test_learning_loop_creates_candidate_for_degrading_profile(monkeypatch):
    import agent.services.planning_learning_loop_service as mod

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
    runs = [
        _make_run(id=f"run-{idx}", planning_profile="lmstudio_laptop", prompt_version_id=str(active_version.id), parse_mode="parse_failed", repair_needed=True, validation_success=False, generated_task_count=0)
        for idx in range(1, 9)
    ]
    reg = _Registry(
        runs=runs,
        profiles=[
            SimpleNamespace(
                profile_name="lmstudio_laptop",
                provider="lmstudio",
                model_name_pattern="gemma",
                model_family="gemma",
                preferred_prompt_version_id=str(active_version.id),
                enabled=True,
            )
        ],
        versions=[active_version],
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    monkeypatch.setattr(
        mod,
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
                        "avg_generated_tasks": 0.0,
                        "output_shape_distribution": {"parse_failed": 1.0},
                        "format_error_distribution": {"truncate": 1.0},
                        "response_behavior_profile": "lmstudio_laptop",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        mod,
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
        mod,
        "get_model_response_behavior_aggregation_service",
        lambda: SimpleNamespace(aggregate=lambda **_: {"observed_run_count": 8, "primary_output_shape_distribution": {"parse_failed": 1.0}}),
    )
    monkeypatch.setattr(mod, "get_planning_review_queue_service", lambda: SimpleNamespace(evaluate_run_for_review=lambda run: []))

    result = PlanningLearningLoopService().run_once(
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

    assert result["candidates_created"] == 1
    assert reg.planning_template_candidate_repo.items
    candidate = reg.planning_template_candidate_repo.items[0]
    assert candidate.status == "proposed"
    assert candidate.candidate_payload["profile_name"] == "lmstudio_laptop"
    assert candidate.candidate_payload["new_prompt_version_id"] == "new-version-id"


def test_learning_loop_rolls_back_regressed_canary(monkeypatch):
    import agent.services.planning_learning_loop_service as mod

    previous_version = PlanningPromptVersionDB(
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
    canary_version = PlanningPromptVersionDB(
        version="v2",
        language="de",
        mode="new_software_project",
        output_contract={},
        system_rules=[],
        user_prompt_template="candidate",
        repair_prompt_template="repair2",
        checksum="chk-2",
        enabled=True,
    )
    canary_candidate = PlanningTemplateCandidateDB(
        source_run_id="run-10",
        goal_type="software_project",
        mode="new_software_project",
        candidate_payload={
            "profile_name": "lmstudio_laptop",
            "current_prompt_version_id": str(previous_version.id),
            "new_prompt_version_id": str(canary_version.id),
            "canary_window_runs": 1,
            "created_at": 1.0,
        },
        confidence="high",
        status="canary",
    )
    reg = _Registry(
        runs=[
            _make_run(id="run-11", planning_profile="lmstudio_laptop", prompt_version_id=str(canary_version.id), parse_mode="parse_failed", repair_needed=True, validation_success=False, generated_task_count=0),
            _make_run(id="run-12", planning_profile="lmstudio_laptop", prompt_version_id=str(canary_version.id), parse_mode="parse_failed", repair_needed=True, validation_success=False, generated_task_count=0),
        ],
        profiles=[
            SimpleNamespace(
                profile_name="lmstudio_laptop",
                provider="lmstudio",
                model_name_pattern="gemma",
                model_family="gemma",
                preferred_prompt_version_id=str(canary_version.id),
                enabled=True,
            )
        ],
        versions=[previous_version, canary_version],
        candidates=[canary_candidate],
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    monkeypatch.setattr(
        mod,
        "get_planning_metrics_service",
        lambda: SimpleNamespace(
            summarize=lambda **_: {
                "run_count": 2,
                "groups": [
                    {
                        "group": "lmstudio::gemma::lmstudio_laptop",
                        "model_key": "lmstudio::gemma",
                        "run_count": 2,
                        "parse_success_rate": 0.0,
                        "repair_rate": 1.0,
                        "validation_success_rate": 0.0,
                        "materialization_success_rate": 0.0,
                        "quality_score": 0.0,
                        "trend_direction": "degrading",
                        "sample_size_is_small": True,
                        "avg_generated_tasks": 0.0,
                        "output_shape_distribution": {"parse_failed": 1.0},
                        "format_error_distribution": {"truncate": 1.0},
                        "response_behavior_profile": "lmstudio_laptop",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_planning_prompt_evolver_service",
        lambda: SimpleNamespace(evolve_from_run=lambda **_: {"evolved": False, "reason": "not_used"}),
    )

    result = PlanningLearningLoopService().run_once(
        planning_policy={
            "learning_loop": {
                "enabled": True,
                "min_runs": 1,
                "min_failures": 1,
                "min_parse_success_rate": 0.9,
                "min_validation_success_rate": 0.9,
                "min_materialization_success_rate": 0.9,
                "max_repair_rate": 0.2,
                "candidate_activation_threshold": 0.95,
                "rollback_threshold": 0.8,
                "freeze_minutes": 1,
                "canary_window_runs": 1,
                "auto_activate": True,
                "require_review_before_activate": False,
            }
        }
    )

    assert result["profiles_rolled_back"] == 1
    assert reg.planning_model_profile_repo.saved[-1].preferred_prompt_version_id == str(previous_version.id)
    assert reg.planning_template_candidate_repo.items[0].status == "rolled_back"
    assert reg.planning_prompt_version_repo.versions[str(canary_version.id)].enabled is False


def test_learning_snapshot_includes_profiles_candidates_and_freeze(monkeypatch):
    import agent.services.planning_learning_loop_service as mod

    previous_version = PlanningPromptVersionDB(
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
    candidate = PlanningTemplateCandidateDB(
        source_run_id="run-1",
        goal_type="software_project",
        mode="new_software_project",
        candidate_payload={
            "profile_name": "lmstudio_laptop",
            "current_prompt_version_id": str(previous_version.id),
            "new_prompt_version_id": "next-version",
            "canary_window_runs": 3,
            "created_at": 100.0,
            "candidate_state": "canary",
        },
        confidence="medium",
        status="canary",
    )
    reg = _Registry(
        runs=[_make_run(id="run-1", planning_profile="lmstudio_laptop", prompt_version_id=str(previous_version.id), parse_mode="strict_json", repair_needed=False, validation_success=True, generated_task_count=2)],
        profiles=[
            SimpleNamespace(
                profile_name="lmstudio_laptop",
                provider="lmstudio",
                model_name_pattern="gemma",
                model_family="gemma",
                preferred_prompt_version_id=str(previous_version.id),
                enabled=True,
            )
        ],
        versions=[previous_version],
        candidates=[candidate],
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    monkeypatch.setattr(
        mod,
        "get_planning_metrics_service",
        lambda: SimpleNamespace(
            summarize=lambda **_: {
                "run_count": 1,
                "groups": [
                    {
                        "group": "lmstudio::gemma::lmstudio_laptop",
                        "model_key": "lmstudio::gemma",
                        "run_count": 1,
                        "parse_success_rate": 1.0,
                        "repair_rate": 0.0,
                        "validation_success_rate": 1.0,
                        "materialization_success_rate": 1.0,
                        "quality_score": 0.75,
                        "trend_direction": "stable",
                        "sample_size_is_small": True,
                        "avg_generated_tasks": 2.0,
                        "output_shape_distribution": {"strict_json": 1.0},
                        "format_error_distribution": {},
                        "response_behavior_profile": "lmstudio_laptop",
                    }
                ],
            }
        ),
    )

    snapshot = mod.get_planning_learning_loop_service().build_snapshot(
        planning_policy={"learning_loop": {"enabled": True, "freeze_minutes": 10, "lookback_runs": 20}}
    )

    assert snapshot["enabled"] is True
    assert snapshot["candidate_count"] == 1
    assert snapshot["review_item_count"] == 0
    assert snapshot["profiles"][0]["profile_name"] == "lmstudio_laptop"
    assert snapshot["profiles"][0]["current_candidate"]["status"] == "canary"
    assert snapshot["profiles"][0]["freeze"]["active"] is True
