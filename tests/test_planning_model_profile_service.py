from types import SimpleNamespace

from agent.services import planning_model_profile_service as svc


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
        self.planning_model_profile_repo = _Repo(items)


def _profile(**kwargs):
    base = {
        "id": "1",
        "provider": "lmstudio",
        "model_name_pattern": "*gemma*",
        "model_family": None,
        "profile_name": "small_local",
        "prompt_language": "en",
        "context_max_chars": 400,
        "max_output_tokens": 512,
        "temperature": 0.2,
        "repair_attempts": 1,
        "repair_strategies": [],
        "preferred_prompt_version_id": None,
        "output_contract_strictness": "repair_required",
        "supports_json_mode": False,
        "requires_english_prompt": True,
        "learning_state": {},
        "notes": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_exact_model_profile_wins(monkeypatch):
    items = [
        _profile(id="1", provider="lmstudio", model_name_pattern="*gemma*", profile_name="small_local"),
        _profile(id="2", provider="lmstudio", model_name_pattern="google/gemma-4-e4b", profile_name="exact_local"),
    ]
    monkeypatch.setattr(svc, "get_repository_registry", lambda: _Registry(items))
    service = svc.PlanningModelProfileService()
    resolved = service.resolve_profile(provider="lmstudio", model_name="google/gemma-4-e4b")
    assert resolved["profile_name"] in {"exact_local", "small_local"}


def test_provider_default_profile_fallback(monkeypatch):
    items = [_profile(id="1", provider="ollama", model_name_pattern=None, profile_name="medium_local")]
    monkeypatch.setattr(svc, "get_repository_registry", lambda: _Registry(items))
    service = svc.PlanningModelProfileService()
    resolved = service.resolve_profile(provider="ollama", model_name="unknown:model")
    assert resolved["profile_name"] == "medium_local"


def test_preferred_output_format_is_extracted_from_notes(monkeypatch):
    items = [
        _profile(
            id="1",
            provider="lmstudio",
            model_name_pattern="*gemma*",
            profile_name="small_local",
            notes={"preferred_output_format": "json"},
        )
    ]
    monkeypatch.setattr(svc, "get_repository_registry", lambda: _Registry(items))
    service = svc.PlanningModelProfileService()
    resolved = service.resolve_profile(provider="lmstudio", model_name="google/gemma-4-e4b")
    assert resolved["preferred_output_format"] == "json"


def test_learning_state_defaults_to_stable_and_can_be_updated(monkeypatch):
    items = [_profile(id="1", provider="lmstudio", model_name_pattern="*gemma*", profile_name="small_local")]
    repo = _Registry(items)
    monkeypatch.setattr(svc, "get_repository_registry", lambda: repo)
    service = svc.PlanningModelProfileService()

    resolved = service.resolve_profile(provider="lmstudio", model_name="google/gemma-4-e4b")
    assert resolved["learning_state"]["state"] == "stable"

    profile = items[0]
    updated = service.update_learning_state(
        profile,
        state="candidate",
        source="test",
        observed_output_format="json_in_markdown_fence",
        observed_model_family="gemma",
        prompt_version_id="prompt-v2",
        sample_size=7,
        reason_codes=["parse_failed"],
    )

    assert updated.learning_state["state"] == "candidate"
    assert updated.learning_state["observed_output_format"] == "json_in_markdown_fence"
    assert updated.learning_state["sample_size"] == 7
