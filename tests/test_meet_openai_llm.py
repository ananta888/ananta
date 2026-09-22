"""OpenAI-compatible companion backend: real loopback transport and a mocked port.

The llama-server endpoint is not reachable from CI, so the backend contract is
exercised against an injected JSON port; the transport itself still talks real
HTTP to a loopback server. Synthetic responses only — no model-quality claim.
"""

import json
import urllib.request
from unittest.mock import Mock

import pytest

from tests.test_meet_ollama_http import server  # noqa: F401 -- explicit reusable loopback fixture
from worker.meet_media import llm, llm_backends, openai_http

pytestmark = pytest.mark.timeout(15)

MODELS = {"object": "list", "data": [{"id": "bonsai-2-27b", "object": "model"}]}


def completion(**changes):
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Synthetische Antwort."}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 8},
    } | changes


class Port:
    """Injected OpenAI JSON port recording the exact call order and payloads."""

    def __init__(self, chat=None, models=None):
        self._chat = completion() if chat is None else chat
        self._models = MODELS if models is None else models
        self.calls = []

    def chat(self, payload):
        self.calls.append(("chat", payload))
        return self._chat

    def models(self):
        self.calls.append(("models", None))
        return self._models


@pytest.fixture
def openai_backend(monkeypatch):
    monkeypatch.setenv("MEET_LLM_BACKEND", "openai")
    monkeypatch.setenv("MEET_LLM_MODEL", "bonsai-2-27b")
    monkeypatch.delenv("MEET_LLM_OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS", raising=False)
    monkeypatch.delenv("MEET_LLM_OPENAI_API_KEY", raising=False)


def test_backend_selection_defaults_to_ollama_and_rejects_an_unknown_name(monkeypatch):
    monkeypatch.delenv("MEET_LLM_BACKEND", raising=False)
    assert llm_backends.backend_name() == "ollama"
    monkeypatch.setenv("MEET_LLM_BACKEND", " OpenAI ")
    assert llm_backends.backend_name() == "openai"
    monkeypatch.setenv("MEET_LLM_BACKEND", "anthropic")
    with pytest.raises(ValueError, match="^meet_llm_backend_unknown$"):
        llm_backends.backend_name()


def test_readiness_gate_runs_before_room_text_is_sent_and_payload_has_no_ollama_options(
    openai_backend,
):
    port = Port()
    generated = llm.generate("untrusted room content", max_output_tokens=8, transport=port)
    assert generated.text == "Synthetische Antwort."
    assert generated.input_tokens == 50 and generated.output_tokens == 8
    assert generated.text not in repr(generated)
    assert [name for name, _payload in port.calls] == ["models", "chat"]
    payload = port.calls[1][1]
    assert set(payload) == {"model", "messages", "stream", "max_tokens", "temperature"}
    assert payload["model"] == "bonsai-2-27b" and payload["max_tokens"] == 8 and payload["stream"] is False
    assert payload["messages"] == [
        {"role": "system", "content": llm.SYSTEM},
        {"role": "user", "content": "untrusted room content"},
    ]


def test_an_unlisted_model_fails_the_gate_before_any_completion_request(openai_backend):
    port = Port(models={"object": "list", "data": [{"id": "some-other-model"}]})
    with pytest.raises(ValueError, match="^meet_llm_model_unavailable$"):
        llm.generate("frage", max_output_tokens=8, transport=port)
    assert [name for name, _payload in port.calls] == ["models"]


@pytest.mark.parametrize(
    "models",
    [{"object": "list", "data": []}, {"object": "list", "data": {}}, {"object": "list"}, {"data": [{"id": ""}]}],
)
def test_an_endpoint_that_serves_nothing_usable_is_not_a_backend(openai_backend, models):
    port = Port(models=models)
    with pytest.raises(ValueError, match="^meet_llm_model_unavailable$"):
        llm.generate("frage", max_output_tokens=8, transport=port)
    assert [name for name, _payload in port.calls] == ["models"]


def test_an_unset_model_takes_the_first_served_entry(openai_backend, monkeypatch):
    monkeypatch.setenv("MEET_LLM_MODEL", "")
    port = Port(models={"object": "list", "data": [{"id": "first-served"}, {"id": "second"}]})
    assert llm.generate("frage", max_output_tokens=8, transport=port).text == "Synthetische Antwort."
    assert port.calls[1][1]["model"] == "first-served"


@pytest.mark.parametrize(
    "chat",
    [
        completion(choices=[{"message": {"content": ""}}]),
        completion(choices=[{"message": {"content": "   "}}]),
        completion(choices=[{"message": {"content": None}}]),
        completion(choices=[{"message": {"role": "assistant"}}]),
        completion(choices=[]),
        completion(choices={}),
        completion(choices=[{"message": "Synthetische Antwort."}]),
    ],
)
def test_an_empty_completion_fails_closed_instead_of_answering_with_nothing(openai_backend, chat):
    with pytest.raises(ValueError, match="^meet_llm_response_invalid$"):
        llm.generate("frage", max_output_tokens=8, transport=Port(chat=chat))


def test_hidden_reasoning_is_never_promoted_to_the_answer(openai_backend):
    reasoning = "SYNTHETIC_HIDDEN_THOUGHT: the user asked about the roadmap."
    chat = completion(
        choices=[{"message": {"role": "assistant", "content": "", "reasoning_content": reasoning}}]
    )
    with pytest.raises(ValueError, match="^meet_llm_response_invalid$") as failure:
        llm.generate("frage", max_output_tokens=8, transport=Port(chat=chat))
    assert "SYNTHETIC_HIDDEN_THOUGHT" not in str(failure.value)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 50},
        {"prompt_tokens": 50, "completion_tokens": 9},
        {"prompt_tokens": 50, "completion_tokens": 0},
        {"prompt_tokens": 50, "completion_tokens": True},
        {"prompt_tokens": 0, "completion_tokens": 8},
        {"prompt_tokens": 16385, "completion_tokens": 8},
        {"prompt_tokens": 1.5, "completion_tokens": 8},
    ],
)
def test_unusable_or_over_budget_usage_is_not_a_success(openai_backend, usage):
    with pytest.raises(ValueError, match="^meet_llm_usage_invalid$"):
        llm.generate("frage", max_output_tokens=8, transport=Port(chat=completion(usage=usage)))


@pytest.mark.parametrize("characters", [1, 10, 450])
def test_reply_budget_markdown_strip_and_untrusted_context_still_apply(openai_backend, characters):
    chat = completion(
        choices=[{"message": {"content": "- **Lang** und `formatiert`: " + "x" * 600}}],
        usage={"prompt_tokens": 50, "completion_tokens": 8},
    )
    port = Port(chat=chat)
    generated = llm.generate(
        "Was ist der Stand?",
        context="SYNTHETIC_INDEX_SNIPPET",
        max_output_tokens=8,
        max_reply_chars=characters,
        transport=port,
    )
    assert 0 < len(generated.text) <= characters
    assert "**" not in generated.text and "`" not in generated.text
    user = port.calls[1][1]["messages"][1]["content"]
    assert user.startswith(llm.CONTEXT_PREFIX) and "SYNTHETIC_INDEX_SNIPPET" in user
    assert user.endswith("Frage: Was ist der Stand?")


def test_reasoning_controls_are_opt_in_and_bounded(openai_backend, monkeypatch):
    port = Port()
    llm.generate("frage", max_output_tokens=8, transport=port)
    assert "reasoning_effort" not in port.calls[1][1]
    monkeypatch.setenv("MEET_LLM_OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": false}')
    port = Port()
    llm.generate("frage", max_output_tokens=8, transport=port)
    assert port.calls[1][1]["reasoning_effort"] == "none"
    assert port.calls[1][1]["chat_template_kwargs"] == {"enable_thinking": False}
    monkeypatch.setenv("MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS", "[1]")
    with pytest.raises(ValueError, match="^meet_llm_chat_template_kwargs_invalid$"):
        llm.generate("frage", max_output_tokens=8, transport=Port())


def test_real_http_transport_uses_the_fixed_openai_paths_and_ignores_proxies(server, monkeypatch):  # noqa: F811
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.delenv("MEET_LLM_OPENAI_API_KEY", raising=False)
    server.replies["/v1/chat/completions"] = (200, {}, json.dumps(completion()).encode())
    server.replies["/v1/models"] = (200, {}, json.dumps(MODELS).encode())
    client = openai_http.OpenAiHttp(server.endpoint + "/v1/")
    assert client.models() == MODELS
    assert client.chat({"synthetic": "untrusted"})["usage"]["completion_tokens"] == 8
    assert [(row[0], row[1]) for row in server.calls] == [
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
    ]


@pytest.mark.parametrize("method,path", [("chat", "/v1/chat/completions"), ("models", "/v1/models")])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_no_redirect_destination_is_contacted_even_on_the_same_origin(server, method, path, status):  # noqa: F811
    server.replies[path] = (status, {"Location": server.endpoint + "/forbidden?synthetic-private"}, b"")
    client = openai_http.OpenAiHttp(server.endpoint + "/v1")
    with pytest.raises(ValueError, match="^meet_llm_transport_failed$") as failure:
        getattr(client, method)(*([{"messages": ["synthetic-private"]}] if method == "chat" else []))
    assert [call[1] for call in server.calls] == [path]
    assert "private" not in str(failure.value)


@pytest.mark.parametrize(
    "body", [b"x" * 65537, b'{"incomplete":', b"[]", b"null", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}']
)
@pytest.mark.parametrize("method,path", [("chat", "/v1/chat/completions"), ("models", "/v1/models")])
def test_both_operations_reject_overflow_and_invalid_json_without_retry(server, body, method, path):  # noqa: F811
    server.replies[path] = (200, {}, body)
    client = openai_http.OpenAiHttp(server.endpoint + "/v1")
    with pytest.raises(ValueError, match="^meet_llm_transport_failed$"):
        getattr(client, method)(*([{}] if method == "chat" else []))
    assert len(server.calls) == 1


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "ftp://localhost/v1",
        "http:///v1",
        "http://private:secret@localhost/v1",
        "http://localhost/v1?token=private",
        "http://localhost/v1#",
        "http://localhost/../v1",
        "http://localhost/v1/../../etc",
        "http://localhost/v1/" + "a" * 130,
        "http://localhost:0/v1",
        "http://localhost:65536/v1",
        "http://localhost:bad/v1",
        " http://localhost/v1",
        "http://local\nhost/v1",
        "http://localhost\\private",
        1,
    ],
)
def test_invalid_operator_base_url_fails_before_creating_a_transport(monkeypatch, base_url):
    opener = Mock()
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    with pytest.raises(ValueError, match="^meet_llm_endpoint_invalid$"):
        openai_http.OpenAiHttp(base_url)
    opener.assert_not_called()


@pytest.mark.parametrize(
    "base_url", ["http://172.18.112.1:8081/v1", "http://172.18.112.1:8081/v1/", "https://host/openai/v1"]
)
def test_a_versioned_base_path_is_accepted_and_normalised(monkeypatch, base_url):
    monkeypatch.delenv("MEET_LLM_OPENAI_API_KEY", raising=False)
    assert openai_http.OpenAiHttp(base_url, opener=Mock()).endpoint == base_url.rstrip("/")


def test_an_operator_key_is_sent_as_a_bearer_header_and_is_optional(monkeypatch):
    monkeypatch.delenv("MEET_LLM_OPENAI_API_KEY", raising=False)
    opener = Mock()
    opener.open.side_effect = ValueError()
    with pytest.raises(ValueError, match="^meet_llm_transport_failed$"):
        openai_http.OpenAiHttp("http://local.test/v1", opener=opener).models()
    assert opener.open.call_args.args[0].get_header("Authorization") is None
    monkeypatch.setenv("MEET_LLM_OPENAI_API_KEY", "synthetic-operator-key")
    opener = Mock()
    opener.open.side_effect = ValueError()
    with pytest.raises(ValueError, match="^meet_llm_transport_failed$"):
        openai_http.OpenAiHttp("http://local.test/v1", opener=opener).models()
    assert opener.open.call_args.args[0].get_header("Authorization") == "Bearer synthetic-operator-key"
    monkeypatch.setenv("MEET_LLM_OPENAI_API_KEY", "bad\nheader-injection")
    with pytest.raises(ValueError, match="^meet_llm_api_key_invalid$"):
        openai_http.OpenAiHttp("http://local.test/v1", opener=Mock())


def test_the_base_url_comes_from_the_operator_environment(monkeypatch, server):  # noqa: F811
    monkeypatch.setenv("MEET_LLM_OPENAI_BASE_URL", server.endpoint + "/v1")
    monkeypatch.delenv("MEET_LLM_OPENAI_API_KEY", raising=False)
    server.replies["/v1/models"] = (200, {}, json.dumps(MODELS).encode())
    assert openai_http.OpenAiHttp().models() == MODELS
    assert [(row[0], row[1]) for row in server.calls] == [("GET", "/v1/models")]
