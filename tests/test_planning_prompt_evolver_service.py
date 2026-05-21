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
        return list(self.versions.values())

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


def test_evolver_is_output_shape_and_family_aware(monkeypatch):
    import agent.services.planning_prompt_evolver_service as mod

    base = PlanningPromptVersionDB(
        version="v1",
        language="de",
        mode="new_software_project",
        target_model_family="gemma",
        output_contract={"expected": "json"},
        system_rules=["be concise"],
        user_prompt_template="Goal: {goal}\n{preferred_output_format}\n{context}",
        repair_prompt_template="Repair: {goal}",
        checksum="chk-base",
        enabled=True,
    )
    reg = _Registry(
        [base],
        [SimpleNamespace(profile_name="lmstudio_laptop", preferred_prompt_version_id=None)],
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)

    run = SimpleNamespace(
        prompt_version_id=str(base.id),
        mode="new_software_project",
        model_name="google/gemma-4-e4b",
        parse_confidence="low",
        repair_attempt_count=3,
        validation_success=False,
        error_classification="truncate",
        planning_profile="lmstudio_laptop",
        parse_mode="parse_failed",
        mode_data={"__output_shape__": "json_in_markdown_fence"},
    )

    result = PlanningPromptEvolverService().evolve_from_run(
        run=run,
        planning_policy={"planner_prompt_evolution": {"enabled": True, "min_repair_attempts": 1}, "preferred_output_format": "json"},
        activate_profile=False,
        enabled=False,
        output_shape="json_in_markdown_fence",
        parse_mode="parse_failed",
        model_family="gemma",
    )

    assert result["evolved"] is True
    saved = reg.planning_prompt_version_repo.saved[0]
    assert "markdown fences" in saved.user_prompt_template
    assert "gemma" in saved.user_prompt_template
    assert saved.output_contract["observed_output_shape"] == "json_in_markdown_fence"
    assert saved.output_contract["observed_parse_mode"] == "parse_failed"
    assert saved.output_contract["observed_model_family"] == "gemma"
