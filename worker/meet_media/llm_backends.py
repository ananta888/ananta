"""Chat backends for the companion: local Ollama or an OpenAI-compatible server.

One adapter per wire protocol (SRP). ``llm`` keeps the prompt, the reply budget
and the plain-text guarantee and depends only on the ``ChatBackend`` port (DIP),
so a new endpoint is an added adapter instead of another branch in ``generate``.

Selected by ``MEET_LLM_BACKEND``: ``ollama`` (default, unchanged behaviour) or
``openai``. Both backends gate on the model actually being served before a reply
is accepted; neither ever falls back to a cloud provider or to hidden reasoning.

Both also run the same bounded tool loop when a ``ToolBox`` is offered (and
first replay any call the router forced, see ``ToolBox.force``): the
model may answer with ``tool_calls``, the tools run here, their results go back
as ``role: "tool"`` messages, and after ``MEET_LLM_TOOL_ROUNDS`` rounds the
tools are withdrawn and the model is told to answer now, so the last request
can only produce an answer. A call the model writes as ``<tool_call>`` text
instead is run like a structured one; on the final request that buys exactly
one more tool-free request, never an open loop.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Protocol

from worker.meet_media import llm_tools
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

    def generate(
        self, *, system: str, user: str, max_output_tokens: int, tools=None
    ) -> RawAnswer: ...


def _usage(input_tokens, output_tokens, *, max_output_tokens):
    if (
        type(input_tokens) is not int
        or not 0 < input_tokens <= context_limit()
        or type(output_tokens) is not int
        or not 0 < output_tokens <= max_output_tokens
    ):
        raise ValueError("meet_llm_usage_invalid")
    return input_tokens, output_tokens


def _tool_calls(message):
    """Well-formed tool calls the model asked for, bounded in number."""
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    usable = [
        call for call in calls if isinstance(call, dict) and isinstance(call.get("function"), dict)
    ]
    return usable[: llm_tools.MAX_CALLS_PER_REPLY]


def _tool_results(tools, calls):
    """Run each call and render its result as a ``role: "tool"`` message.

    ``tool_call_id`` is echoed when the server supplied one (OpenAI); Ollama
    correlates by name instead, so the id is simply absent there.
    """
    messages = []
    for call in calls:
        function = call["function"]
        name = function.get("name")
        content = tools.run(name, function.get("arguments"))
        message = {"role": "tool", "name": str(name or "")[:64], "content": content}
        identifier = call.get("id")
        if isinstance(identifier, str) and identifier:
            message["tool_call_id"] = identifier[:128]
        messages.append(message)
    return messages


def _forced(tools, *, json_arguments):
    """Router-forced tool round (``ToolBox.force``), replayed before the first request."""
    forced = getattr(tools, "forced_messages", None)
    return forced(json_arguments=json_arguments) if forced is not None else []


def _rounds(tools):
    """Tool rounds available for this reply (0 when no tool is offered)."""
    return llm_tools.max_rounds() if tools is not None and tools.definitions() else 0


def _final_messages(messages):
    """History for the tool-free final request.

    After a tool round the model still tends to write the next call as
    ``<tool_call>`` text even though no tool is offered any more, so the
    request closes with the explicit instruction to answer now. A reply
    without any tool round is sent unchanged.
    """
    if any(message.get("role") == "tool" for message in messages):
        return [*messages, {"role": "user", "content": llm_tools.FINAL_ANSWER}]
    return messages


def _leaked(tools, message, *, json_arguments, turn):
    """A tool call the model wrote as text, shaped like a structured one."""
    if tools is None:
        return []
    calls = []
    for index, (name, arguments) in enumerate(llm_tools.leaked_calls(message.get("content"))):
        entry = {
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False) if json_arguments else arguments,
            },
        }
        if json_arguments:
            entry["id"] = "leaked-%d-%d" % (turn, index)
        calls.append(entry)
    return calls


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

    def generate(self, *, system, user, max_output_tokens, tools=None):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            *_forced(tools, json_arguments=False),
        ]
        gated = False
        # Reported usage is summed over the rounds; each single round is still
        # validated against the same per-request budget.
        input_total = output_total = 0
        remaining = _rounds(tools)
        # One extra tool-free request if the final answer is a leaked call.
        repair = tools is not None
        while True:
            final = remaining <= 0
            payload = {
                "model": self.model,
                "messages": _final_messages(messages) if final else messages,
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
            if not final:
                payload["tools"] = tools.definitions()
            result = self.transport.chat(payload)
            if not gated:
                self._require_loaded()
                gated = True
            message = result.get("message")
            if not isinstance(message, dict) or result.get("done") is not True:
                raise ValueError("meet_llm_response_invalid")
            input_tokens, output_tokens = _usage(
                result.get("prompt_eval_count"),
                result.get("eval_count"),
                max_output_tokens=max_output_tokens,
            )
            input_total += input_tokens
            output_total += output_tokens
            # Structured calls are only honoured while tools are offered; the
            # final request may buy one repair round for a leaked text call.
            calls = [] if final else _tool_calls(message)
            # A leaked call's text is the call itself, never part of an answer.
            said = (message.get("content") or "") if calls else ""
            calls = calls or _leaked(tools, message, json_arguments=False, turn=len(messages))
            if calls and (not final or repair):
                repair = repair and not final
                messages.append({"role": "assistant", "content": said, "tool_calls": calls})
                messages.extend(_tool_results(tools, calls))
                remaining -= 1
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("meet_llm_response_invalid")
            return RawAnswer(content, input_total, output_total, self.model)

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

    def generate(self, *, system, user, max_output_tokens, tools=None):
        model = self._serving_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            *_forced(tools, json_arguments=True),
        ]
        # Reported usage is summed over the rounds; each single round is still
        # validated against the same per-request budget.
        input_total = output_total = 0
        remaining = _rounds(tools)
        # One extra tool-free request if the final answer is a leaked call.
        repair = tools is not None
        while True:
            final = remaining <= 0
            payload = {
                "model": model,
                "messages": _final_messages(messages) if final else messages,
                "stream": False,
                "max_tokens": max_output_tokens,
                "temperature": temperature(),
                **_reasoning_controls(),
            }
            if not final:
                # Withdrawn on the last round (no ``tools``, no ``tool_choice``):
                # the final request can only answer, see ``_final_messages``.
                payload["tools"] = tools.definitions()
                payload["tool_choice"] = "auto"
            result = self.transport.chat(payload)
            message = _message(result)
            usage = result.get("usage")
            if not isinstance(usage, dict):
                raise ValueError("meet_llm_usage_invalid")
            input_tokens, output_tokens = _usage(
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                max_output_tokens=max_output_tokens,
            )
            input_total += input_tokens
            output_total += output_tokens
            # Structured calls are only honoured while tools are offered; the
            # final request may buy one repair round for a leaked text call.
            calls = [] if final else _tool_calls(message)
            # A leaked call's text is the call itself, never part of an answer.
            said = (message.get("content") or "") if calls else ""
            calls = calls or _leaked(tools, message, json_arguments=True, turn=len(messages))
            if calls and (not final or repair):
                repair = repair and not final
                messages.append({"role": "assistant", "content": said, "tool_calls": calls})
                messages.extend(_tool_results(tools, calls))
                remaining -= 1
                continue
            return RawAnswer(_require_content(message), input_total, output_total, model)

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


def _message(result):
    """The single assistant message of a non-streamed completion."""
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("meet_llm_response_invalid")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("meet_llm_response_invalid")
    return message


def _content(result):
    return _require_content(_message(result))


def _require_content(message):
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
