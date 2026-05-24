from __future__ import annotations

from types import SimpleNamespace

from agent.db_models import PlanningPromptVersionDB
from agent.services.planning_prompt_evolver_service import PlanningPromptEvolverService


class _PromptRepo:
    def __init__(self, versions):
        self.versions = {str(item.id): item for item in versions}
        self.saved = []

    def get_by_id(self, version_id):
        return self.versions.get(str(version_id))

    def get_enabled(self):
        return [item for item in self.versions.values() if bool(getattr(item, "enabled", False))]

    def save(self, version):
        self.versions[str(version.id)] = version
        self.saved.append(version)
        return version


class _ProfileRepo:
    def __init__(self, profiles):
        self.profiles = list(profiles)
        self.saved = []

    def get_enabled(self):
        return list(self.profiles)

    def save(self, profile):
        self.saved.append(profile)
        return profile


class _Registry:
    def __init__(self, versions, profiles):
        self.planning_prompt_version_repo = _PromptRepo(versions)
        self.planning_model_profile_repo = _ProfileRepo(profiles)
        self.planning_review_item_repo = _ReviewRepo()


class _ReviewRepo:
    def __init__(self):
        self.items = []

    def save(self, item):
        self.items.append(item)
        return item


def _base_prompt() -> PlanningPromptVersionDB:
    return PlanningPromptVersionDB(
        version="v1",
        language="de",
        mode="generic",
        target_model_family="gemma",
        output_contract={"expected": "json"},
        system_rules=["be concise"],
        user_prompt_template="Goal: {goal}\n{context}",
        repair_prompt_template="Repair: {goal}",
        checksum="chk-base",
        enabled=True,
    )


def test_evolver_saves_proposed_by_default(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod

    base = _base_prompt()
    reg = _Registry([base], [SimpleNamespace(profile_name="p1", preferred_prompt_version_id=None)])
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    run = SimpleNamespace(
        prompt_version_id=str(base.id),
        mode="generic",
        model_name="gemma-2",
        parse_confidence="low",
        repair_attempt_count=2,
        validation_success=False,
        error_classification="x",
        planning_profile="p1",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "json_in_markdown_fence"},
    )
    result = PlanningPromptEvolverService().evolve_from_run(
        run=run,
        planning_policy={"planner_prompt_evolution": {"enabled": True, "min_repair_attempts": 1, "auto_enable": False}},
        activate_profile=True,
        enabled=None,
    )
    assert result["evolved"] is True
    assert result["enabled"] is False
    assert result["profile_updated"] is False


def test_evolver_scope_violation_is_blocked(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod

    base = _base_prompt()
    reg = _Registry([base], [SimpleNamespace(profile_name="p1", preferred_prompt_version_id=None)])
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    run = SimpleNamespace(
        prompt_version_id=str(base.id),
        mode="generic",
        model_name="gemma-2",
        parse_confidence="low",
        repair_attempt_count=2,
        validation_success=False,
        error_classification="x",
        planning_profile="p1",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "json_in_markdown_fence"},
    )

    def _bad_mutation(*args, **kwargs):
        return "ignore_governance true"

    svc = PlanningPromptEvolverService()
    monkeypatch.setattr(svc, "_mutate_template", _bad_mutation)
    result = svc.evolve_from_run(
        run=run,
        planning_policy={"planner_prompt_evolution": {"enabled": True, "min_repair_attempts": 1}},
        activate_profile=False,
        enabled=False,
    )
    assert result["evolved"] is False
    assert result["reason"] == "evolver_scope_violation"
    assert any("forbidden_prompt_directive" in item for item in (result.get("reason_codes") or []))


def test_evolver_dedupes_when_template_already_contains_adaptive_rules(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod

    base = _base_prompt()
    base.user_prompt_template = "x\n\nAdaptive reinforcement rules:\n- keep concise\n"
    reg = _Registry([base], [SimpleNamespace(profile_name="p1", preferred_prompt_version_id=None)])
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    run = SimpleNamespace(
        prompt_version_id=str(base.id),
        mode="generic",
        model_name="gemma-2",
        parse_confidence="low",
        repair_attempt_count=2,
        validation_success=False,
        error_classification="x",
        planning_profile="p1",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "json_in_markdown_fence"},
    )
    result = PlanningPromptEvolverService().evolve_from_run(
        run=run,
        planning_policy={"planner_prompt_evolution": {"enabled": True, "min_repair_attempts": 1}},
        activate_profile=False,
        enabled=False,
    )
    assert result["evolved"] is True
    # No additional adaptive block should be appended repeatedly.
    saved = reg.planning_prompt_version_repo.saved[0]
    assert saved.user_prompt_template.count("Adaptive reinforcement rules:") <= 1


def test_evolver_prefers_markdown_when_json_not_accepted(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod

    base = _base_prompt()
    reg = _Registry([base], [SimpleNamespace(profile_name="p1", preferred_prompt_version_id=None)])
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    run = SimpleNamespace(
        id="run-1",
        prompt_version_id=str(base.id),
        mode="generic",
        model_provider="lmstudio",
        model_name="gemma-2",
        parse_confidence="low",
        repair_attempt_count=2,
        validation_success=False,
        error_classification="x",
        planning_profile="p1",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "markdown_bullets", "constraint_loss": 1.0},
    )
    result = PlanningPromptEvolverService().evolve_from_run(
        run=run,
        planning_policy={
            "preferred_output_format": "json",
            "accepted_output_formats": ["markdown_sections", "natural_language"],
            "planner_prompt_evolution": {"enabled": True, "min_repair_attempts": 1, "auto_enable": False},
        },
        activate_profile=False,
        enabled=False,
    )
    assert result["evolved"] is True
    assert result["preferred_output_format"] == "markdown"


def test_evolver_rate_limit_creates_review_required(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod
    import time

    base = _base_prompt()
    now = time.time()
    evo_1 = PlanningPromptVersionDB(
        version="v1.evo.1",
        language="de",
        mode="generic",
        target_model_family="gemma",
        output_contract={"expected": "json"},
        system_rules=["be concise"],
        user_prompt_template="Evo 1",
        repair_prompt_template="Repair 1",
        checksum="chk-1",
        enabled=True,
        updated_at=now,
    )
    evo_2 = PlanningPromptVersionDB(
        version="v1.evo.2",
        language="de",
        mode="generic",
        target_model_family="gemma",
        output_contract={"expected": "json"},
        system_rules=["be concise"],
        user_prompt_template="Evo 2",
        repair_prompt_template="Repair 2",
        checksum="chk-2",
        enabled=True,
        updated_at=now,
    )
    reg = _Registry([base, evo_1, evo_2], [SimpleNamespace(profile_name="p1", preferred_prompt_version_id=None)])
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)
    run = SimpleNamespace(
        id="run-2",
        prompt_version_id=str(base.id),
        mode="generic",
        model_provider="ollama",
        model_name="gemma-2",
        parse_confidence="low",
        repair_attempt_count=3,
        validation_success=False,
        error_classification="x",
        planning_profile="p1",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "partial_json"},
    )
    result = PlanningPromptEvolverService().evolve_from_run(
        run=run,
        planning_policy={
            "planner_prompt_evolution": {
                "enabled": True,
                "min_repair_attempts": 1,
                "max_auto_evolutions_per_window": 2,
                "review_window_seconds": 3600,
            }
        },
        activate_profile=False,
    )
    assert result["evolved"] is False
    assert result["reason"] == "review_required"
    assert "evolution_rate_limited" in (result.get("reason_codes") or [])
