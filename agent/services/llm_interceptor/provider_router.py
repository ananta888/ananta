from __future__ import annotations

from typing import Any, Iterable

import requests

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig, UpstreamConfig


class ProviderRouter:
    """Routes and forwards normalized chat requests to configured upstreams."""

    def __init__(self, cfg: LlmInterceptorConfig) -> None:
        self.cfg = cfg
        self._by_id = {u.id: u for u in cfg.upstreams}

    def resolve_upstream(self, *, model: str | None = None) -> UpstreamConfig:
        chosen = self._by_id[self.cfg.routing.default_upstream]
        if model:
            for up in self.cfg.upstreams:
                if up.allowed_models and model in up.allowed_models:
                    chosen = up
                    break
        return chosen

    def _enforce_model_allowlist(self, upstream: UpstreamConfig, model: str) -> None:
        if upstream.allowed_models and model not in upstream.allowed_models:
            raise ValueError("model_not_allowed_for_upstream")

    def forward_chat(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        model = str(payload.get("model") or "").strip() or self.cfg.routing.default_model
        upstream = self.resolve_upstream(model=model)
        self._enforce_model_allowlist(upstream, model)
        target = f"{upstream.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if upstream.api_key_env:
            import os

            key = str(os.getenv(upstream.api_key_env) or "").strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        body = dict(payload)
        body["model"] = model
        resp = requests.post(target, json=body, headers=headers, timeout=upstream.timeout_seconds)
        if resp.status_code >= 400:
            raise ValueError(f"upstream_error:{resp.status_code}")
        try:
            return dict(resp.json())
        except ValueError as exc:
            raise ValueError("upstream_invalid_json") from exc

    def forward_chat_stream(self, *, payload: dict[str, Any]) -> Iterable[str]:
        model = str(payload.get("model") or "").strip() or self.cfg.routing.default_model
        upstream = self.resolve_upstream(model=model)
        self._enforce_model_allowlist(upstream, model)
        target = f"{upstream.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        body = dict(payload)
        body["model"] = model
        body["stream"] = True
        resp = requests.post(target, json=body, headers=headers, timeout=upstream.timeout_seconds, stream=True)
        if resp.status_code >= 400:
            raise ValueError(f"upstream_error:{resp.status_code}")
        for line in resp.iter_lines(decode_unicode=True):
            if line is None:
                continue
            text = str(line).strip()
            if not text:
                continue
            yield f"{text}\n\n"

