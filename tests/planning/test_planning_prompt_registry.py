from types import SimpleNamespace

from agent.services import planning_prompt_registry as reg


class _Repo:
    def __init__(self, items):
        self._items = items

    def get_enabled(self):
        return self._items

    def save(self, item):
        self._items.append(item)
        return item


class _Registry:
    def __init__(self, items):
        self.planning_prompt_version_repo = _Repo(items)


def _version(**kwargs):
    base = {
        "id": "v1",
        "version": "v1",
        "language": "en",
        "mode": "new_software_project",
        "target_model_family": None,
        "output_contract": {},
        "system_rules": [],
        "user_prompt_template": "GOAL={goal} CONTEXT={context}",
        "repair_prompt_template": "repair {goal}",
        "checksum": "abc",
        "enabled": True,
        "prompt_suffix": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_prompt_version_is_resolved_for_model_profile(monkeypatch):
    items = [_version()]
    monkeypatch.setattr(reg, "get_repository_registry", lambda: _Registry(items))
    registry = reg.PlanningPromptRegistry()
    resolved = registry.resolve(goal="Build API", context="ctx", mode="new_software_project", language="en", model_family=None)
    assert resolved.prompt_version_id == "v1"


def test_new_project_prompt_contains_structured_contract(monkeypatch):
    items = [_version(user_prompt_template="Plan project for {goal}. Include artifacts and verification. Context: {context}")]
    monkeypatch.setattr(reg, "get_repository_registry", lambda: _Registry(items))
    registry = reg.PlanningPromptRegistry()
    resolved = registry.resolve(goal="Build API", context="ctx", mode="new_software_project", language="en", model_family=None)
    assert "artifacts" in resolved.prompt.lower() or "verification" in resolved.prompt.lower()


def test_new_project_prompt_accepts_markdown_friendly_guidance(monkeypatch):
    items = [_version(user_prompt_template="Plan project for {goal}. Markdown fences are acceptable if they help. Context: {context}")]
    monkeypatch.setattr(reg, "get_repository_registry", lambda: _Registry(items))
    registry = reg.PlanningPromptRegistry()
    resolved = registry.resolve(goal="Build API", context="ctx", mode="new_software_project", language="en", model_family=None)
    assert "Markdown fences are acceptable" in resolved.prompt


def test_prompt_registry_adapts_to_learning_state_and_behavior_profile(monkeypatch):
    items = [_version(user_prompt_template="Plan project for {goal}. Context: {context}")]
    monkeypatch.setattr(reg, "get_repository_registry", lambda: _Registry(items))
    registry = reg.PlanningPromptRegistry()
    resolved = registry.resolve(
        goal="Build API",
        context="ctx",
        mode="new_software_project",
        language="en",
        model_family=None,
        preferred_output_format="json",
        behavior_profile={
            "preferred_prompt_style": "example_driven_json",
            "learning_state": {"state": "candidate", "observed_output_format": "json_in_markdown_fence"},
        },
    )
    assert "learning phase" in resolved.prompt.lower()
    assert "markdown fences" in resolved.prompt.lower()
