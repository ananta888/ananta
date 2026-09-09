"""Closed terminal validation for pinned Pi's single-turn, no-tools JSON mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PiProtocolError(ValueError):
    """A fixed diagnostic without model output or credentials."""


def _require(condition: bool, reason: str = "pi_event_contract_invalid") -> None:
    if not condition:
        raise PiProtocolError(reason)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise PiProtocolError("pi_event_contract_invalid")


class _OneShotEvents:
    def __init__(self, *, provider: str, model: str, workspace: Path):
        self.provider, self.model, self.workspace = provider, model, str(workspace)
        self.phase = "initial"
        self.active_role: str | None = None
        self.messages: list[dict[str, Any]] = []
        self.streaming_terminal: dict[str, Any] | None = None
        self.answer = ""

    def feed(self, event: dict[str, Any]) -> None:
        handlers = {
            "session": self._session,
            "agent_start": self._agent_start,
            "turn_start": self._turn_start,
            "message_start": self._message_start,
            "message_update": self._message_update,
            "message_end": self._message_end,
            "turn_end": self._turn_end,
            "agent_end": self._agent_end,
            "agent_settled": self._agent_settled,
        }
        kind = event.get("type")
        _require(isinstance(kind, str))
        _require(not kind.startswith("tool_"), "pi_tool_execution_denied")
        handler = handlers.get(kind)
        _require(handler is not None)
        handler(event)

    def _session(self, event: dict[str, Any]) -> None:
        _require(self.phase == "initial" and type(event.get("version")) is int and event["version"] == 3)
        _require(event.get("cwd") == self.workspace, "pi_workspace_binding_mismatch")
        self.phase = "session"

    def _agent_start(self, _event: dict[str, Any]) -> None:
        _require(self.phase == "session")
        self.phase = "agent"

    def _turn_start(self, _event: dict[str, Any]) -> None:
        _require(self.phase == "agent")
        self.phase = "turn"

    def _message_start(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        _require(self.phase == "turn" and self.active_role is None and len(self.messages) < 2)
        _require(isinstance(message, dict))
        expected = "user" if not self.messages else "assistant"
        _require(message.get("role") == expected)
        self.active_role = expected

    def _message_update(self, event: dict[str, Any]) -> None:
        _require(self.phase == "turn" and self.active_role == "assistant")
        update = event.get("assistantMessageEvent")
        _require(isinstance(update, dict) and isinstance(update.get("type"), str))
        kind = update["type"]
        _require(not kind.startswith("toolcall_"), "pi_tool_execution_denied")
        _require(kind != "error", "pi_assistant_failed")
        _require(kind in {
            "start", "text_start", "text_delta", "text_end", "thinking_start", "thinking_delta", "thinking_end", "done",
        })
        if kind == "done":
            _require(self.streaming_terminal is None, "pi_terminal_conflict")
            _require(update.get("reason") == "stop", "pi_assistant_failed")
            message = update.get("message")
            _require(isinstance(message, dict) and message.get("role") == "assistant")
            self._assistant_text(message)
            self.streaming_terminal = message

    def _message_end(self, event: dict[str, Any]) -> None:
        _require(self.phase == "turn" and self.active_role is not None)
        message = event.get("message")
        _require(isinstance(message, dict) and message.get("role") == self.active_role)
        if self.active_role == "assistant":
            self.answer = self._assistant_text(message)
            _require(
                self.streaming_terminal is None or self.streaming_terminal == message, "pi_terminal_conflict"
            )
        self.messages.append(message)
        self.active_role = None

    def _assistant_text(self, message: dict[str, Any]) -> str:
        _require(message.get("stopReason") == "stop" and not message.get("errorMessage"), "pi_assistant_failed")
        _require(
            message.get("provider") == self.provider and message.get("model") == self.model
            and message.get("api") == "openai-completions",
            "pi_model_binding_mismatch",
        )
        content = message.get("content")
        _require(isinstance(content, list))
        text = []
        for block in content:
            _require(isinstance(block, dict))
            kind = block.get("type")
            _require(kind != "toolCall", "pi_tool_execution_denied")
            _require(kind in {"text", "thinking"})
            value = block.get("text" if kind == "text" else "thinking")
            _require(isinstance(value, str))
            if kind == "text":
                text.append(value)
        answer = "".join(text)
        _require(bool(answer.strip()), "pi_assistant_response_missing")
        return answer

    def _turn_end(self, event: dict[str, Any]) -> None:
        _require(self.phase == "turn" and self.active_role is None and len(self.messages) == 2)
        _require(event.get("message") == self.messages[-1], "pi_terminal_conflict")
        _require(event.get("toolResults") == [], "pi_tool_execution_denied")
        self.phase = "turn_ended"

    def _agent_end(self, event: dict[str, Any]) -> None:
        _require(self.phase == "turn_ended")
        _require(event.get("willRetry") is False, "pi_retry_not_authorized")
        _require(event.get("messages") == self.messages, "pi_terminal_conflict")
        self.phase = "agent_ended"

    def _agent_settled(self, _event: dict[str, Any]) -> None:
        _require(self.phase == "agent_ended")
        self.phase = "settled"


def parse_pi_one_shot(stdout: str, *, provider: str, model: str, workspace: Path) -> str:
    """Accept only one complete pinned JSON lifecycle; never issue evidence IDs."""
    _require(len(stdout) <= 4_000_000, "pi_event_output_limit")
    state = _OneShotEvents(provider=provider, model=model, workspace=workspace)
    try:
        count = 0
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            count += 1
            _require(count <= 16_384, "pi_event_output_limit")
            event = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            _require(isinstance(event, dict))
            state.feed(event)
        _require(state.phase == "settled", "pi_terminal_missing")
    except (json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise PiProtocolError("pi_event_contract_invalid") from exc
    return state.answer
