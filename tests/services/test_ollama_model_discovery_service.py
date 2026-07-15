from __future__ import annotations

import json

from agent.services.integration_registry_service import IntegrationRegistryService
from agent.services.ollama_model_discovery_service import OllamaModelDiscoveryService


def test_discovery_normalizes_generate_url_and_uses_runtime_tags() -> None:
    calls: list[tuple[str, int]] = []

    def probe(base_url: str, timeout: int):
        calls.append((base_url, timeout))
        return {
            "ok": True,
            "status": "ok",
            "models": [
                {"name": "qwen2.5:7b"},
                {"name": "llama3.2:3b"},
                {"name": "qwen2.5:7b"},
            ],
        }

    service = OllamaModelDiscoveryService(probe=probe)

    result = service.discover(
        base_url="http://ollama:11434/api/generate",
        configured_models=["llama3", "mistral"],
        timeout_seconds=9,
        force_refresh=True,
    )

    assert calls == [("http://ollama:11434", 9)]
    assert result.available is True
    assert result.status == "ok"
    assert result.used_configured_fallback is False
    assert [item["id"] for item in result.models] == ["qwen2.5:7b", "llama3.2:3b"]
    assert all(item["source"] == "ollama_api_tags" for item in result.models)


def test_discovery_keeps_configured_models_as_unavailable_fallback() -> None:
    service = OllamaModelDiscoveryService(
        probe=lambda _url, _timeout: {
            "ok": False,
            "status": "error",
            "models": [],
        }
    )

    result = service.discover(
        base_url="http://ollama:11434/api/generate",
        configured_models=["llama3", "mistral", "llama3", ""],
        force_refresh=True,
    )

    assert result.available is False
    assert result.status == "error"
    assert result.used_configured_fallback is True
    assert [item["id"] for item in result.models] == ["llama3", "mistral"]
    assert all(item["available"] is False for item in result.models)
    assert all(item["source"] == "configured_fallback" for item in result.models)


def test_discovery_cache_and_force_refresh_are_injectable() -> None:
    now = [100.0]
    calls = 0

    def probe(_url: str, _timeout: int):
        nonlocal calls
        calls += 1
        return {"ok": True, "status": "ok", "models": [{"name": f"model-{calls}"}]}

    service = OllamaModelDiscoveryService(probe=probe, monotonic=lambda: now[0])
    first = service.discover(base_url="http://ollama:11434", cache_ttl_seconds=60)
    second = service.discover(base_url="http://ollama:11434", cache_ttl_seconds=60)
    refreshed = service.discover(
        base_url="http://ollama:11434",
        cache_ttl_seconds=60,
        force_refresh=True,
    )

    assert calls == 2
    assert first == second
    assert first.models[0]["id"] == "model-1"
    assert refreshed.models[0]["id"] == "model-2"


def test_default_discovery_disables_proxies_redirects_and_bounds_model_count(monkeypatch) -> None:
    calls: list[dict] = []

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, *, chunk_size: int):
            assert chunk_size == 64 * 1024
            yield json.dumps(
                {"models": [{"name": f"model-{index}"} for index in range(100)]}
            ).encode()

    class _Session:
        trust_env = True

        def get(self, url: str, **kwargs):
            calls.append({"url": url, **kwargs, "trust_env": self.trust_env})
            return _Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "agent.services.ollama_model_discovery_service.requests.Session",
        _Session,
    )

    result = OllamaModelDiscoveryService().discover(
        base_url="http://ollama.local:11434/proxy/api/generate",
        force_refresh=True,
    )

    assert result.available is True
    assert len(result.models) == 64
    assert calls[0]["url"] == "http://ollama.local:11434/proxy/api/tags"
    assert calls[0]["trust_env"] is False
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["stream"] is True


def test_default_discovery_rejects_redirects(monkeypatch) -> None:
    class _Response:
        status_code = 302
        headers: dict[str, str] = {"Location": "http://untrusted.invalid/api/tags"}

        def raise_for_status(self) -> None:
            return None

    class _Session:
        trust_env = True

        def get(self, _url: str, **_kwargs):
            return _Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "agent.services.ollama_model_discovery_service.requests.Session",
        _Session,
    )

    result = OllamaModelDiscoveryService().discover(
        base_url="http://ollama.local:11434",
        configured_models=["fallback"],
        force_refresh=True,
    )

    assert result.available is False
    assert result.status == "redirect_forbidden"
    assert [item["id"] for item in result.models] == ["fallback"]


def test_integration_registry_marks_ollama_local_dynamic_and_openai_compatible() -> None:
    discovery = OllamaModelDiscoveryService(
        probe=lambda _url, _timeout: {
            "ok": True,
            "status": "ok",
            "models": [{"name": "qwen2.5:7b"}],
        }
    )
    registry = IntegrationRegistryService(ollama_model_discovery=discovery)

    specs = registry.list_inference_provider_specs(
        agent_cfg={},
        provider_urls={"ollama": "http://ollama:11434/api/generate"},
        default_provider="ollama",
        default_model="qwen2.5:7b",
    )
    ollama = next(item for item in specs if item["provider"] == "ollama")

    assert ollama["provider_type"] == "local_openai_compatible"
    assert ollama["transport_provider"] == "ollama"
    assert ollama["capabilities"] == {
        "dynamic_models": True,
        "supports_chat": True,
        "openai_compatible": True,
        "transport_provider": "ollama",
        "provider_type": "local_openai_compatible",
        "locality": "local",
    }

    models = registry.list_openai_compat_models(
        agent_cfg={},
        provider_urls={"ollama": "http://ollama:11434/api/generate"},
        default_provider="ollama",
        default_model="qwen2.5:7b",
        model_lister=lambda _url, timeout: [],
    )
    ollama_ids = {item["id"] for item in models if item["provider"] == "ollama"}
    assert ollama_ids == {"ollama:qwen2.5:7b"}
