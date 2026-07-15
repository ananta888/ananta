from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
import requests

from ananta_contracts.voice_corrector_worker import VoiceCorrectorWorkerRequest
from worker.runtime.generative_corrector_app import create_app
from worker.runtime.generative_corrector_provider_engine import (
    CorrectorProviderEndpoint,
    ProviderGenerativeCorrectorEngine,
)


class _Response:
    def __init__(
        self,
        payload: object | None = None,
        *,
        body: bytes | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._body = body if body is not None else json.dumps(payload).encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"fixture HTTP {self.status_code}")

    def iter_content(self, *, chunk_size: int):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]


class _Session:
    def __init__(
        self,
        *,
        get_responses: dict[str, _Response] | None = None,
        post_response: _Response | None = None,
    ) -> None:
        self.trust_env = True
        self._get_responses = dict(get_responses or {})
        self._post_response = post_response
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.get_calls.append((url, kwargs))
        return self._get_responses[url]

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.post_calls.append((url, kwargs))
        if self._post_response is None:
            raise AssertionError("unexpected provider POST")
        return self._post_response


class _SequencedDiscoverySession:
    def __init__(
        self,
        *,
        initial_response: _Response,
        refresh_response: _Response | Exception,
        block_initial: bool = False,
    ) -> None:
        self.trust_env = True
        self._initial_response = initial_response
        self._refresh_response = refresh_response
        self._block_initial = block_initial
        self._lock = threading.Lock()
        self.get_call_count = 0
        self.initial_started = threading.Event()
        self.initial_release = threading.Event()
        self.refresh_started = threading.Event()
        self.refresh_release = threading.Event()
        self.refresh_returned = threading.Event()

    def get(self, _url: str, **_kwargs: Any) -> _Response:
        with self._lock:
            self.get_call_count += 1
            call_number = self.get_call_count
        if call_number == 1:
            self.initial_started.set()
            if self._block_initial:
                assert self.initial_release.wait(timeout=2.0)
            return self._initial_response
        self.refresh_started.set()
        try:
            assert self.refresh_release.wait(timeout=0.75)
            if isinstance(self._refresh_response, Exception):
                raise self._refresh_response
            return self._refresh_response
        finally:
            self.refresh_returned.set()


def _request(model_id: str, *, original_text: str = "hallo welt") -> VoiceCorrectorWorkerRequest:
    return VoiceCorrectorWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        original_text=original_text,
        model_id=model_id,
        language="de",
        max_edit_ratio=0.5,
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def test_discovers_lmstudio_and_ollama_models_from_fixed_admin_endpoints() -> None:
    session = _Session(
        get_responses={
            "http://lmstudio:1234/v1/models": _Response({"data": [{"id": "org/model"}, {"id": "qwen2.5-coder"}]}),
            "http://ollama:11434/api/tags": _Response(
                {
                    "models": [
                        {
                            "name": "qwen2.5:7b",
                            "digest": "sha256:0123456789abcdef",
                        }
                    ]
                }
            ),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [
            CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234/v1"),
            CorrectorProviderEndpoint("ollama", "http://ollama:11434/api/generate"),
        ],
        session=session,  # type: ignore[arg-type]
    )

    assert engine.provider_ids == ("lmstudio", "ollama")
    assert engine.ready_provider_ids == ("lmstudio", "ollama")
    assert engine.model_ids == (
        "lmstudio:org/model",
        "lmstudio:qwen2.5-coder",
        "ollama:qwen2.5:7b",
    )
    assert engine.health_snapshot() == {
        "model_ids": (
            "lmstudio:org/model",
            "lmstudio:qwen2.5-coder",
            "ollama:qwen2.5:7b",
        ),
        "provider_ids": ("lmstudio", "ollama"),
        "ready_provider_ids": ("lmstudio", "ollama"),
    }
    assert session.trust_env is False
    assert all(call[1]["allow_redirects"] is False for call in session.get_calls)
    assert all(call[1]["stream"] is True for call in session.get_calls)


def test_initial_catalog_load_is_synchronous_single_flight_and_deterministic() -> None:
    session = _SequencedDiscoverySession(
        initial_response=_Response({"models": [{"name": "qwen2.5:7b"}, {"name": "qwen2.5:14b"}]}),
        refresh_response=_Response({"models": []}),
        block_initial=True,
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("ollama", "http://ollama:11434")],
        session=session,  # type: ignore[arg-type]
        discovery_ttl_seconds=3_600,
    )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(lambda: engine.model_ids) for _index in range(6)]
        try:
            assert session.initial_started.wait(timeout=1.0)
            time.sleep(0.05)
            assert all(not future.done() for future in futures)
        finally:
            session.initial_release.set()
        results = [future.result(timeout=1.0) for future in futures]

    assert (
        results
        == [
            ("ollama:qwen2.5:7b", "ollama:qwen2.5:14b"),
        ]
        * 6
    )
    assert session.get_call_count == 1


def test_catalog_bound_does_not_skip_later_provider_readiness_checks() -> None:
    session = _Session(
        get_responses={
            "http://lmstudio:1234/v1/models": _Response({"data": [{"id": f"model-{index}"} for index in range(65)]}),
            "http://ollama:11434/api/tags": _Response({"models": []}),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [
            CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234"),
            CorrectorProviderEndpoint("ollama", "http://ollama:11434"),
        ],
        session=session,  # type: ignore[arg-type]
    )

    assert len(engine.model_ids) == 64
    assert engine.ready_provider_ids == ("lmstudio", "ollama")
    assert len(session.get_calls) == 2


def test_expired_catalog_returns_stale_health_immediately_and_refreshes_once() -> None:
    session = _SequencedDiscoverySession(
        initial_response=_Response({"models": [{"name": "qwen2.5:7b"}]}),
        refresh_response=_Response({"models": [{"name": "qwen3:8b"}]}),
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("ollama", "http://ollama:11434")],
        session=session,  # type: ignore[arg-type]
        discovery_ttl_seconds=0.01,
    )
    assert engine.model_ids == ("ollama:qwen2.5:7b",)
    assert engine.ready_provider_ids == ("ollama",)
    time.sleep(0.02)
    client = create_app(
        engine=engine,
        auth_token="corrector-secret-at-least-24-characters",
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()

    started_at = time.monotonic()
    try:
        response = client.get("/health")
        elapsed = time.monotonic() - started_at

        assert response.status_code == 200
        assert response.json["status"] == "ready"
        assert response.json["model_ids"] == ["ollama:qwen2.5:7b"]
        assert response.json["provider_ids"] == ["ollama"]
        assert response.json["ready_provider_ids"] == ["ollama"]
        assert elapsed < 0.25
        assert session.refresh_started.wait(timeout=1.0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            parallel_results = list(pool.map(lambda _index: engine.model_ids, range(8)))
        assert parallel_results == [("ollama:qwen2.5:7b",)] * 8
        assert session.get_call_count == 2
        engine._discovery_ttl = 3_600
    finally:
        session.refresh_release.set()

    assert session.refresh_returned.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while engine.model_ids != ("ollama:qwen3:8b",) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine.model_ids == ("ollama:qwen3:8b",)
    assert engine.ready_provider_ids == ("ollama",)
    assert session.get_call_count == 2


def test_unresolvable_background_refresh_invalidates_stale_catalog_and_readiness() -> None:
    session = _SequencedDiscoverySession(
        initial_response=_Response({"data": [{"id": "org/model"}]}),
        refresh_response=requests.ConnectTimeout("fixture provider timeout"),
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234")],
        session=session,  # type: ignore[arg-type]
        discovery_ttl_seconds=0.01,
    )
    assert engine.model_ids == ("lmstudio:org/model",)
    assert engine.ready_provider_ids == ("lmstudio",)
    time.sleep(0.02)

    started_at = time.monotonic()
    try:
        assert engine.model_ids == ("lmstudio:org/model",)
        assert time.monotonic() - started_at < 0.25
        assert session.refresh_started.wait(timeout=1.0)
        assert session.get_call_count == 2
        engine._discovery_ttl = 3_600
    finally:
        session.refresh_release.set()

    assert session.refresh_returned.wait(timeout=1.0)
    time.sleep(0.05)
    assert engine.model_ids == ()
    assert engine.ready_provider_ids == ()
    assert session.get_call_count == 2


def test_executes_a_manually_selected_qualified_ollama_model_with_strict_json() -> None:
    session = _Session(
        get_responses={"http://ollama:11434/api/tags": _Response({"models": []})},
        post_response=_Response(
            {"choices": [{"message": {"content": ('{"schema_version":"1.0","corrected_text":"Hallo Welt."}')}}]}
        ),
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("ollama", "http://ollama:11434")],
        session=session,  # type: ignore[arg-type]
        discovery_ttl_seconds=3_600,
    )

    assert engine.supports_model("ollama:org/model") is True
    outcome = engine.correct(_request("ollama:org/model"))

    assert outcome.corrected_text == "Hallo Welt."
    assert outcome.model_id == "ollama:org/model"
    assert outcome.model_revision == "runtime-unpinned"
    assert outcome.engine_id == "ollama-http"
    url, call = session.post_calls[0]
    assert url == "http://ollama:11434/v1/chat/completions"
    assert call["json"]["model"] == "org/model"
    assert call["json"]["stream"] is False
    assert call["allow_redirects"] is False
    assert call["stream"] is True


@pytest.mark.parametrize(
    "configured_url",
    [
        "http://gateway.internal:11434/tenant/local-ollama/api/generate",
        "http://gateway.internal:11434/tenant/local-ollama/v1/chat/completions",
    ],
)
def test_ollama_gateway_subpath_is_preserved_for_discovery_and_correction(
    configured_url: str,
) -> None:
    discovery_url = "http://gateway.internal:11434/tenant/local-ollama/api/tags"
    correction_url = "http://gateway.internal:11434/tenant/local-ollama/v1/chat/completions"
    session = _Session(
        get_responses={discovery_url: _Response({"models": [{"name": "qwen2.5:7b"}]})},
        post_response=_Response(
            {"choices": [{"message": {"content": ('{"schema_version":"1.0","corrected_text":"Hallo Welt."}')}}]}
        ),
    )
    endpoint = CorrectorProviderEndpoint("ollama", configured_url)
    engine = ProviderGenerativeCorrectorEngine(
        [endpoint],
        session=session,  # type: ignore[arg-type]
        discovery_ttl_seconds=3_600,
    )

    outcome = engine.correct(_request("ollama:qwen2.5:7b"))

    assert endpoint.base_url == "http://gateway.internal:11434/tenant/local-ollama"
    assert outcome.corrected_text == "Hallo Welt."
    assert session.get_calls[0][0] == discovery_url
    assert session.post_calls[0][0] == correction_url


@pytest.mark.parametrize(
    "content, error",
    [
        ("Hallo Welt.", "invalid JSON"),
        (
            '{"schema_version":"1.0","corrected_text":"Hallo Welt.","extra":true}',
            "response schema",
        ),
    ],
)
def test_rejects_non_contract_provider_output(content: str, error: str) -> None:
    session = _Session(
        post_response=_Response({"choices": [{"message": {"content": content}}]}),
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234")],
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match=error):
        engine.correct(_request("lmstudio:org/model"))


def test_forbids_execution_redirects_and_bounds_provider_responses() -> None:
    redirect_session = _Session(post_response=_Response({}, status_code=302))
    redirect_engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234")],
        session=redirect_session,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="redirect is forbidden"):
        redirect_engine.correct(_request("lmstudio:org/model"))

    oversized_session = _Session(post_response=_Response(body=b"x" * 4_097))
    oversized_engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234")],
        session=oversized_session,  # type: ignore[arg-type]
        response_max_bytes=4_096,
    )

    with pytest.raises(ValueError, match="byte limit"):
        oversized_engine.correct(_request("lmstudio:org/model"))


def test_redirected_discovery_is_not_followed_or_advertised() -> None:
    session = _Session(
        get_responses={
            "http://ollama:11434/api/tags": _Response({}, status_code=307),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("ollama", "http://ollama:11434")],
        session=session,  # type: ignore[arg-type]
    )

    assert engine.model_ids == ()
    assert engine.ready_provider_ids == ()
    assert session.get_calls[0][1]["allow_redirects"] is False


def test_offline_configured_provider_is_degraded_but_remains_identifiable() -> None:
    session = _Session(
        get_responses={
            "http://lmstudio:1234/v1/models": _Response({}, status_code=503),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234")],
        session=session,  # type: ignore[arg-type]
    )
    client = create_app(
        engine=engine,
        auth_token="corrector-secret-at-least-24-characters",
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json["status"] == "degraded"
    assert health.json["model_ids"] == []
    assert health.json["provider_ids"] == ["lmstudio"]
    assert health.json["ready_provider_ids"] == []


def test_partial_discovery_reports_only_the_reachable_provider_as_ready() -> None:
    session = _Session(
        get_responses={
            "http://lmstudio:1234/v1/models": _Response({"data": [{"id": "org/model"}]}),
            "http://ollama:11434/api/tags": _Response({}, status_code=503),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [
            CorrectorProviderEndpoint("lmstudio", "http://lmstudio:1234"),
            CorrectorProviderEndpoint("ollama", "http://ollama:11434"),
        ],
        session=session,  # type: ignore[arg-type]
    )
    client = create_app(
        engine=engine,
        auth_token="corrector-secret-at-least-24-characters",
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json["status"] == "ready"
    assert health.json["model_ids"] == ["lmstudio:org/model"]
    assert health.json["provider_ids"] == ["lmstudio", "ollama"]
    assert health.json["ready_provider_ids"] == ["lmstudio"]


def test_reachable_provider_with_empty_catalog_is_ready_for_manual_execution() -> None:
    session = _Session(
        get_responses={
            "http://ollama:11434/api/tags": _Response({"models": []}),
        }
    )
    engine = ProviderGenerativeCorrectorEngine(
        [CorrectorProviderEndpoint("ollama", "http://ollama:11434")],
        session=session,  # type: ignore[arg-type]
    )
    client = create_app(
        engine=engine,
        auth_token="corrector-secret-at-least-24-characters",
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json["status"] == "ready"
    assert health.json["model_ids"] == []
    assert health.json["provider_ids"] == ["ollama"]
    assert health.json["ready_provider_ids"] == ["ollama"]
