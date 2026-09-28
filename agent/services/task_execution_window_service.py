"""The context window of the runtime that will execute a task, predicted by the Hub before it splits it.

The Ananta context profile (``agent.context_profile``) is the window of local runtimes. A task that a
subscription/cloud agent (claude-cli, codex/opencode/aider against a cloud model, the profile CLIs) or a cloud
LLM provider will process has that model's window -- splitting it into local-sized pieces would only cost
calls. The Hub predicts the runtime with the same resolvers the worker uses at propose time
(``resolve_task_cli_backend`` over the Hub config merged with the goal snapshot, then ``routing_dimensions``).
If a worker decides differently, the runtime-overflow handling (LCTX-009) still catches a too-large task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

# the inference target kinds of routing_dimensions: local runtime vs. remote/cloud models
_LOCAL_TARGETS = frozenset({"local_openai"})
_CLOUD_TARGETS = frozenset({"remote_openai_compatible", "native_opencode"})
_CLI_LIMIT_NAMES = {"claude_code": "claude", "codex": "codex", "opencode": "opencode"}
_DEFAULT_CLOUD_WINDOW = 128_000


@dataclass(frozen=True)
class ExecutionWindow:
    tokens: int
    backend: str
    provider: str | None
    model: str | None
    local: bool
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "backend": self.backend, "provider": self.provider, "model": self.model,
                "local": self.local, "reason": self.reason}


def _scoped_config(task: Mapping[str, Any], agent_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Hub config merged with the goal's effective config -- what the worker's propose uses."""
    cfg = dict(agent_cfg or {})
    goal_id = str(task.get("goal_id") or "").strip() or None
    if not goal_id:
        return cfg
    try:
        from agent.services.goal_config_runtime_service import get_goal_config_runtime_service

        scoped = get_goal_config_runtime_service().get_effective_config(goal_id=goal_id, task_id=task.get("id"))
        cfg.update({key: value for key, value in dict(scoped.config or {}).items() if value is not None})
    except Exception:  # noqa: BLE001 -- no goal snapshot: the Hub config
        _log.debug("goal config unavailable for %s", goal_id, exc_info=True)
    return cfg


def _as_mapping(task: Any) -> dict[str, Any]:
    if isinstance(task, Mapping):
        return dict(task)
    if hasattr(task, "model_dump"):
        return dict(task.model_dump())
    return {key: getattr(task, key) for key in ("id", "goal_id", "task_kind", "title", "description",
                                                 "worker_execution_context") if hasattr(task, key)}


def execution_window(task: Any, agent_cfg: Mapping[str, Any] | None = None) -> ExecutionWindow:
    """The window of the runtime predicted for ``task``; the local effective window when nothing better is known."""
    from agent.context_profile import cloud_model_limit, effective_window_tokens, is_local_provider

    local_window = effective_window_tokens(agent_cfg=agent_cfg)
    try:
        from agent.cli_backends.budget import prompt_token_limit
        from agent.cli_backends.routing import PROFILE_CLI_BACKENDS
        from agent.runtime_policy import normalize_task_kind
        from agent.services._task_scoped_runtime import resolve_task_cli_backend, routing_dimensions
        from agent.services.worker_routing_policy_utils import derive_required_capabilities

        data = _as_mapping(task)
        cfg = _scoped_config(data, agent_cfg or {})
        task_kind = normalize_task_kind(data.get("task_kind"), str(data.get("description") or data.get("title") or ""))
        backend, _reason = resolve_task_cli_backend(task_kind=task_kind, agent_cfg=cfg,
                                                    required_capabilities=derive_required_capabilities(data, task_kind))
        backend = str(backend or "").strip().lower()
        model = str(cfg.get("default_model") or "").strip() or None
        dims = routing_dimensions(backend_used=backend, model=model, agent_cfg=cfg)
        provider = str(dims.get("inference_provider") or "").strip().lower() or None
        target_model = str(dims.get("inference_model") or model or "").strip() or None
        target_kind = str(dims.get("inference_target_kind") or "").strip().lower() or None
    except Exception as exc:  # noqa: BLE001 -- unpredictable: size for the local window
        _log.debug("execution window prediction failed: %s", exc)
        return ExecutionWindow(local_window, "unknown", None, None, True, "prediction_failed")

    if backend == "claude_code" or backend in PROFILE_CLI_BACKENDS:
        limit = prompt_token_limit(_CLI_LIMIT_NAMES.get(backend, "claude"), model=target_model) \
            if backend == "claude_code" else (cloud_model_limit(target_model, _DEFAULT_CLOUD_WINDOW) or _DEFAULT_CLOUD_WINDOW)
        cli_provider = "anthropic" if backend == "claude_code" else None  # the CLI's own account, not Ananta's
        return ExecutionWindow(limit, backend, cli_provider, target_model if backend != "claude_code" else None,
                               False, "subscription_cli")
    if target_kind in _LOCAL_TARGETS or (target_kind is None and is_local_provider(provider, cfg)):
        return ExecutionWindow(effective_window_tokens(provider=provider, agent_cfg=cfg), backend, provider,
                               target_model, True, "local_runtime")
    if target_kind in _CLOUD_TARGETS or (provider and not is_local_provider(provider, cfg)):
        limit = cloud_model_limit(target_model, None) or cloud_model_limit(provider, _DEFAULT_CLOUD_WINDOW) \
            or _DEFAULT_CLOUD_WINDOW
        if backend in _CLI_LIMIT_NAMES:
            limit = min(limit, prompt_token_limit(_CLI_LIMIT_NAMES[backend], model=target_model, local=False))
        return ExecutionWindow(limit, backend, provider, target_model, False, "cloud_model")
    if backend in _CLI_LIMIT_NAMES:  # target not resolvable here: the CLI gate's model rule decides
        limit = prompt_token_limit(_CLI_LIMIT_NAMES[backend], model=target_model)
        return ExecutionWindow(limit, backend, provider, target_model, limit <= local_window, "cli_model")
    return ExecutionWindow(local_window, backend, provider, target_model, True, "unknown_target")


__all__ = ["ExecutionWindow", "execution_window"]
