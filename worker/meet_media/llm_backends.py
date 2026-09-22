"""Chat backends for the companion: local Ollama or an OpenAI-compatible server.

One adapter per wire protocol (SRP). ``llm`` keeps the prompt, the reply budget
and the plain-text guarantee and depends only on the ``ChatBackend`` port (DIP),
so a new endpoint is an added adapter instead of another branch in ``generate``.

Selected by ``MEET_LLM_BACKEND``: ``ollama`` (default, unchanged behaviour) or
``openai``. Both backends gate on the model actually being served before a reply
is accepted; neither ever falls back to a cloud provider or to hidden reasoning.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Protocol

from worker.meet_media.ollama_http import OllamaHttp
from worker.meet_media.openai_http import OpenAiHttp

OLLAMA = "ollama"
OPENAI = "openai"
BACKENDS = (OLLAMA, OPENAI)

DEFAULT_OLLAMA_MODEL = "spark-x2.5-4b-q8-128k:latest"
DEFAULT_OLLAMA_DIGEST = "006077d1c2f168ae439a3443b9675081b5f49a03ae7c6ce875ea5fbe9cb9e9bb"

_ANNOUNCED = set()
_MAX_ANNOUNCEMENTS = 32


def announce(message):
    """Report one operator-visible fact once per process, on stderr.

    The image runs with ``PYTHONUNBUFFERED=1``, so this lands in the container
    log next to the companion's own trace. Only configuration is reported here —
    never room text, a reply or an API key.
    """
    if message in _ANNOUNCED:
        return
    if len(_ANNOUNCED) < _MAX_ANNOUNCEMENTS:
        _ANNOUNCED.add(message)
    print("meet_llm " + message, file=sys.stderr, flush=True)


def context_limit():
    """Accepted prompt size. Ollama is told this; an OpenAI server owns its own."""
    return int(os.environ.get("MEET_LLM_NUM_CTX", "16384"))


def temperature():
    return float(os.environ.get("MEET_LLM_TEMPERATURE", "0.3"))


def configured_model():
    return (os.environ.get("MEET_LLM_MODEL") or "").strip()


@dataclass(frozen=True)
class RawAnswer:
    """Validated backend reply before the shared reply budget is applied."""

    text: str = field(repr=False)
    input_tokens: int
    output_tokens: int
    model: str


class ChatBackend(Protocol):
    name: str
    description: str

    def generate(self, *, system: str, user: str, max_output_tokens: int) -> RawAnswer: ...


def _usage(input_tokens, output_tokens, *, max_output_tokens):
    if (
        type(input_tokens) is not int
        or not 0 < input_tokens <= context_limit()
        or type(output_tokens) is not int
        or not 0 < output_tokens <= max_output_tokens
    ):
        raise ValueError("meet_llm_usage_invalid")
    return input_tokens, output_tokens


class OllamaBackend:
    """Local Ollama ``/api/chat``, gated on the model being resident on the GPU."""

    name = OLLAMA

    def __init__(self, transport=None):
        self.transport = transport if transport is not None else OllamaHttp()
        self.model = configured_model() or DEFAULT_OLLAMA_MODEL

    @property
    def description(self):
        return "backend=%s endpoint=%s model=%s" % (
            self.name,
            getattr(self.transport, "endpoint", "injected"),
            self.model,
        )

    def generate(self, *, system, user, max_output_tokens):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # Reasoning models otherwise spend the whole output budget on hidden
            # thinking and leave the answer content empty.
            "think": False,
            "keep_alive": "5m",
            "options": {
                "num_ctx": context_limit(),
                "num_predict": max_output_tokens,
                "temperature": temperature(),
                "num_gpu": int(os.environ.get("MEET_LLM_NUM_GPU", "99")),
            },
        }
        result = self.transport.chat(payload)
        self._require_loaded()
        content = result.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip() or result.get("done") is not True:
            raise ValueError("meet_llm_response_invalid")
        input_tokens, output_tokens = _usage(
            result.get("prompt_eval_count"),
            result.get("eval_count"),
            max_output_tokens=max_output_tokens,
        )
        return RawAnswer(content, input_tokens, output_tokens, self.model)

    def _require_loaded(self):
        digest = os.environ.get("MEET_LLM_DIGEST", DEFAULT_OLLAMA_DIGEST)
        loaded = self.transport.models()
        if not any(
            item.get("name") == self.model and item.get("digest") == digest
            for item in loaded.get("models", [])
        ):
            raise ValueError("meet_llm_gpu_required")


def _reasoning_controls():
    """Opt-in knobs for a server whose model reasons by default.

    An OpenAI-compatible server keeps hidden reasoning out of ``content`` (it
    reports ``reasoning_content`` instead), so a thinking model would answer
    with an empty string. Nothing is sent unless an operator asks for it:
    ``MEET_LLM_OPENAI_REASONING_EFFORT`` (e.g. ``none``) sets
    ``reasoning_effort``; ``MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS`` takes a JSON
    object (e.g. ``{"enable_thinking": false}``) for chat-template arguments.
    """
    controls = {}
    effort = (os.environ.get("MEET_LLM_OPENAI_REASONING_EFFORT") or "").strip()
    if effort:
        controls["reasoning_effort"] = effort
    raw = (os.environ.get("MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise ValueError("meet_llm_chat_template_kwargs_invalid") from None
        if not isinstance(parsed, dict):
            raise ValueError("meet_llm_chat_template_kwargs_invalid")
        controls["chat_template_kwargs"] = parsed
    return controls


class OpenAiBackend:
    """OpenAI-compatible ``/chat/completions``, gated on ``GET /models``.

    Sampling limits that Ollama receives per request (``num_ctx``, ``num_gpu``)
    are server-side settings here and are deliberately not sent.
    """

    name = OPENAI

    def __init__(self, transport=None):
        self.transport = transport if transport is not None else OpenAiHttp()

    @property
    def description(self):
        return "backend=%s endpoint=%s model=%s" % (
            self.name,
            getattr(self.transport, "endpoint", "injected"),
            configured_model() or "<first listed>",
        )

    def generate(self, *, system, user, max_output_tokens):
        model = self._serving_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "max_tokens": max_output_tokens,
            "temperature": temperature(),
            **_reasoning_controls(),
        }
        result = self.transport.chat(payload)
        content = _content(result)
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("meet_llm_usage_invalid")
        input_tokens, output_tokens = _usage(
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            max_output_tokens=max_output_tokens,
        )
        return RawAnswer(content, input_tokens, output_tokens, model)

    def _serving_model(self):
        """Readiness gate: the endpoint must already serve the chosen model."""
        listing = self.transport.models()
        data = listing.get("data")
        if not isinstance(data, list):
            raise ValueError("meet_llm_model_unavailable")
        served = [
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]
        wanted = configured_model()
        if wanted:
            if wanted not in served:
                raise ValueError("meet_llm_model_unavailable")
            return wanted
        if not served:
            raise ValueError("meet_llm_model_unavailable")
        announce("openai model resolved=%s (MEET_LLM_MODEL unset, served=%s)" % (served[0], served))
        return served[0]


def _content(result):
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("meet_llm_response_invalid")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("meet_llm_response_invalid")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        # A reasoning-only reply (``reasoning_content`` without ``content``) is
        # a server configuration failure, not an answer: hidden thinking must
        # never be spoken, and an empty reply must not reach the room either.
        raise ValueError("meet_llm_response_invalid")
    return content


def backend_name():
    name = (os.environ.get("MEET_LLM_BACKEND") or OLLAMA).strip().lower()
    if name not in BACKENDS:
        raise ValueError("meet_llm_backend_unknown")
    return name


def select(transport=None):
    """Build the configured backend; ``transport`` injects its JSON port."""
    backend = OpenAiBackend(transport) if backend_name() == OPENAI else OllamaBackend(transport)
    announce(backend.description)
    return backend
