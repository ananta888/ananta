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


def test_prompt_suffix_is_extracted_from_notes(monkeypatch):
    items = [
        _profile(
            id="1",
            provider="lmstudio",
            model_name_pattern="*gemma*",
            profile_name="small_local",
            notes={"preferred_output_format": "json", "prompt_suffix": "Use short JSON only."},
        )
    ]
    monkeypatch.setattr(svc, "get_repository_registry", lambda: _Registry(items))
    service = svc.PlanningModelProfileService()
    resolved = service.resolve_profile(provider="lmstudio", model_name="google/gemma-4-e4b")
    assert resolved["preferred_output_format"] == "json"
    assert resolved["prompt_suffix"] == "Use short JSON only."
