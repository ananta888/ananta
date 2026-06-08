"""OpenRouterAdapter — OpenRouter.ai API (SIM-018).

Reads OPENROUTER_API_KEY from environment.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from simulation.adapters.base import AdapterResponse, SimulationModelAdapter
from simulation.models.action import ActionProposal


class OpenRouterAdapter(SimulationModelAdapter):

    def __init__(self, model: str = "openai/gpt-4o-mini",
                  base_url: str = "https://openrouter.ai/api/v1",
                  timeout: float = 60.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def provider(self) -> str:
        return "openrouter"

    @property
    def model_id(self) -> str:
        return f"openrouter/{self._model}"

    def generate(self, messages: list[dict[str, str]],
                  agent_id: str, **kwargs: Any) -> AdapterResponse:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            fallback = ActionProposal.invalid_fallback(agent_id, "missing_api_key")
            return AdapterResponse(raw_text="", proposal=fallback,
                                    parse_error="OPENROUTER_API_KEY not set",
                                    model_id=self.model_id)
        try:
            import urllib.request
            payload = json.dumps({
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            }).encode()
            req = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://github.com/ananta",
                },
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read())
            latency_ms = (time.monotonic() - t0) * 1000

            raw_text = body["choices"][0]["message"]["content"]
            tokens = body.get("usage", {}).get("total_tokens", 0)
            cost = body.get("usage", {}).get("cost", 0.0)
            return self._parse(raw_text, agent_id, latency_ms, tokens, float(cost))
        except Exception as exc:
            fallback = ActionProposal.invalid_fallback(agent_id, str(exc))
            return AdapterResponse(raw_text="", proposal=fallback,
                                    parse_error=str(exc), model_id=self.model_id)

    def _parse(self, raw: str, agent_id: str, latency_ms: float,
                tokens: int, cost: float) -> AdapterResponse:
        try:
            data = json.loads(raw)
            data.setdefault("agent_id", agent_id)
            proposal = ActionProposal.model_validate(data)
            return AdapterResponse(raw_text=raw, proposal=proposal,
                                    tokens_used=tokens, cost_usd=cost,
                                    latency_ms=latency_ms, model_id=self.model_id)
        except Exception as exc:
            fallback = ActionProposal.invalid_fallback(agent_id, raw[:80])
            return AdapterResponse(raw_text=raw, proposal=fallback,
                                    parse_error=str(exc), tokens_used=tokens,
                                    cost_usd=cost, latency_ms=latency_ms,
                                    model_id=self.model_id)
