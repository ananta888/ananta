from __future__ import annotations

from typing import Any, Iterable

import requests

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig, UpstreamConfig


class ProviderRouter:
    """Routes and forwards normalized chat requests to configured upstreams."""

    def __init__(self, cfg: LlmInterceptorConfig) -> None:
        self.cfg = cfg
        self._by_id = {u.id: u for u in cfg.upstreams}

    def _matches_when(self, when: dict[str, Any], meta: dict[str, Any]) -> bool:
        for key, value in dict(when or {}).items():
            if key == "risk_lte":
                order = ["low", "medium", "high", "critical"]
                req = str(value).lower()
                got = str(meta.get("risk") or "medium").lower()
                if req not in order or got not in order or order.index(got) > order.index(req):
                    return False
                continue
            if meta.get(key) != value:
                return False
        return True

    def resolve_route(self, *, payload: dict[str, Any], envelope: dict[str, Any] | None = None) -> tuple[UpstreamConfig, str]:
        env = dict(envelope or {})
        caller = dict(env.get("caller_metadata") or payload.get("caller") or {})
        task = dict(env.get("task_metadata") or payload.get("task") or {})
        route_meta = {
            "worker": str(caller.get("worker") or caller.get("source") or "").lower(),
            "task_kind": str(task.get("task_kind") or "").lower(),
            "risk": str(task.get("risk") or "medium").lower(),
            "requires_cloud": bool(task.get("requires_cloud", False)),
            "context_class": str(task.get("context_class") or "").lower(),
        }
        chosen_upstream_id = self.cfg.routing.default_upstream
        requested_model = str(payload.get("model") or self.cfg.routing.default_model).strip()
        chosen_model = str(self.cfg.routing.model_aliases.get(requested_model) or requested_model)
        for rule in self.cfg.routing.rules:
            if self._matches_when(rule.when, route_meta):
                chosen_upstream_id = rule.upstream
                if rule.model:
                    chosen_model = rule.model
                break
        upstream = self._by_id[chosen_upstream_id]
        # Worker-supplied model is mapped through allowlist, not blindly trusted.
        if upstream.allowed_models and chosen_model not in upstream.allowed_models:
            chosen_model = upstream.allowed_models[0]
        return upstream, chosen_model

    def _enforce_model_allowlist(self, upstream: UpstreamConfig, model: str) -> None:
        if upstream.allowed_models and model not in upstream.allowed_models:
            raise ValueError("model_not_allowed_for_upstream")

    def forward_chat(self, *, payload: dict[str, Any], envelope: dict[str, Any] | None = None) -> dict[str, Any]:
        upstream, model = self.resolve_route(payload=payload, envelope=envelope)
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

    def forward_chat_stream(self, *, payload: dict[str, Any], envelope: dict[str, Any] | None = None) -> Iterable[str]:
        upstream, model = self.resolve_route(payload=payload, envelope=envelope)
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
