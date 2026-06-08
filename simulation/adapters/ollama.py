"""OllamaAdapter — local Ollama HTTP API (SIM-017).

Requires `ollama` package or raw HTTP. Falls back gracefully if not installed.
"""
from __future__ import annotations

import json
import time
from typing import Any

from simulation.adapters.base import AdapterResponse, SimulationModelAdapter
from simulation.models.action import ActionProposal


class OllamaAdapter(SimulationModelAdapter):
    """Calls a local Ollama server at http://localhost:11434."""

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434",
                  timeout: float = 60.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model_id(self) -> str:
        return f"ollama/{self._model}"

    def generate(self, messages: list[dict[str, str]],
                  agent_id: str, **kwargs: Any) -> AdapterResponse:
        try:
            import urllib.request
            payload = json.dumps({
                "model": self._model,
                "messages": messages,
                "stream": False,
                "format": "json",
            }).encode()
            req = urllib.request.Request(
                f"{self._base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read())
            latency_ms = (time.monotonic() - t0) * 1000

            raw_text = body.get("message", {}).get("content", "")
            return self._parse(raw_text, agent_id, latency_ms,
                                body.get("eval_count", 0))
        except Exception as exc:
            fallback = ActionProposal.invalid_fallback(agent_id, str(exc))
            return AdapterResponse(raw_text="", proposal=fallback,
                                    parse_error=str(exc), model_id=self.model_id)

    def _parse(self, raw: str, agent_id: str, latency_ms: float,
                tokens: int) -> AdapterResponse:
        try:
            data = json.loads(raw)
            data.setdefault("agent_id", agent_id)
            proposal = ActionProposal.model_validate(data)
            return AdapterResponse(raw_text=raw, proposal=proposal,
                                    tokens_used=tokens, latency_ms=latency_ms,
                                    model_id=self.model_id)
        except Exception as exc:
            fallback = ActionProposal.invalid_fallback(agent_id, raw[:80])
            return AdapterResponse(raw_text=raw, proposal=fallback,
                                    parse_error=str(exc), tokens_used=tokens,
                                    latency_ms=latency_ms, model_id=self.model_id)
