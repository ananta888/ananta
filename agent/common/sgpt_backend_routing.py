from __future__ import annotations

import logging
import shutil
import sys
import time
from typing import Any

from agent.config import settings
from agent.local_llm_backends import get_local_openai_backends, resolve_local_openai_backend
from agent.research_backend import (
    RESEARCH_BACKEND_PROVIDERS,
    get_research_backend_preflight,
    is_research_backend,
    resolve_research_backend_config,
)
from agent.cli_backends.helpers import (
    _classify_runtime_target,
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_openai_base_url,
)

log = logging.getLogger(__name__)

SUPPORTED_CLI_BACKENDS = {"sgpt", "ananta-worker", "codex", "opencode", "aider", "mistral_code", *RESEARCH_BACKEND_PROVIDERS}
CLI_BACKEND_INSTALL_HINTS = {
    "sgpt": "python -m pip install shell-gpt",
    "ananta-worker": "python -m pip install shell-gpt",
    "codex": "npm i -g @openai/codex",
    "opencode": "npm i -g opencode-ai",
    "aider": "python -m pip install aider-chat",
    "mistral_code": "npm i -g mistral-code",
    "deerflow": "Clone deer-flow and configure research_backend.command plus research_backend.working_dir.",
    "ananta_research": "Install or clone ananta_research and configure research_backend.command plus research_backend.working_dir.",
}
CLI_BACKEND_VERIFY_COMMANDS = {
    "sgpt": "python -m sgpt --help",
    "ananta-worker": "python -m sgpt --help",
    "codex": "codex --help",
    "opencode": "opencode --help",
    "aider": "aider --help",
    "mistral_code": "mistral-code --help",
    "deerflow": "python main.py --help",
    "ananta_research": "configure research_backend.command",
}
CLI_BACKEND_CAPABILITIES = {
    "sgpt": {
        "display_name": "ShellGPT",
        "supports_model": True,
        "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "ananta-worker": {
        "display_name": "Ananta Worker (internal)",
        "supports_model": True,
        "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "codex": {
        "display_name": "OpenAI Codex CLI",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "opencode": {
        "display_name": "OpenCode",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "aider": {
        "display_name": "Aider",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "mistral_code": {
        "display_name": "Mistral Code",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "deerflow": {
        "display_name": "DeerFlow",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "ananta_research": {
        "display_name": "ananta_research",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "browser_use": {
        "display_name": "browser_use",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
}

_BACKEND_RUNTIME: dict[str, dict] = {
    name: {
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "cooldown_until": 0.0,
        "total_success": 0,
        "total_failures": 0,
        "last_error": "",
        "last_rc": None,
        "last_latency_ms": None,
    }
    for name in SUPPORTED_CLI_BACKENDS
}


def _resolve_backend_binary(backend: str) -> str | None:
    if is_research_backend(backend):
        return resolve_research_backend_config(provider_override=backend).get("binary_path")
    if backend in {"sgpt", "ananta-worker"}:
        return sys.executable if sys.executable else None
    if backend == "codex":
        return shutil.which(settings.codex_path or "codex")
    if backend == "opencode":
        return shutil.which(settings.opencode_path or "opencode")
    if backend == "aider":
        return shutil.which(settings.aider_path or "aider")
    if backend == "mistral_code":
        return shutil.which(settings.mistral_code_path or "mistral-code")
    return None


def _configured_backend_command(backend: str) -> str:
    if is_research_backend(backend):
        return str(resolve_research_backend_config(provider_override=backend).get("command") or "")
    if backend in {"sgpt", "ananta-worker"}:
        return f"{sys.executable} -m sgpt" if sys.executable else "python -m sgpt"
    if backend == "codex":
        return settings.codex_path or "codex"
    if backend == "opencode":
        return settings.opencode_path or "opencode"
    if backend == "aider":
        return settings.aider_path or "aider"
    if backend == "mistral_code":
        return settings.mistral_code_path or "mistral-code"
    return ""


def _health_score(backend: str) -> int:
    rt = _BACKEND_RUNTIME.get(backend, {})
    score = 100
    if not _resolve_backend_binary(backend):
        score -= 80
    score -= min(40, int(rt.get("consecutive_failures", 0)) * 10)
    cooldown_until = float(rt.get("cooldown_until") or 0.0)
    if cooldown_until > time.time():
        score -= 20
    if rt.get("last_latency_ms") and rt["last_latency_ms"] > 30000:
        score -= 10
    return max(0, min(100, score))


def get_cli_backend_runtime_status() -> dict[str, dict]:
    from agent.common.sgpt_opencode import resolve_codex_runtime_config, resolve_opencode_runtime_config

    now = time.time()
    data: dict[str, dict] = {}
    for name in sorted(SUPPORTED_CLI_BACKENDS):
        rt = dict(_BACKEND_RUNTIME.get(name, {}))
        cooldown_until = float(rt.get("cooldown_until") or 0.0)
        runtime_entry = {
            "binary_path": _resolve_backend_binary(name),
            "binary_available": bool(_resolve_backend_binary(name)),
            "health_score": _health_score(name),
            "cooldown_active": cooldown_until > now,
            "cooldown_until": cooldown_until,
            **rt,
        }
        if name == "codex":
            codex_runtime = resolve_codex_runtime_config()
            runtime_entry["target_base_url"] = codex_runtime["base_url"]
            runtime_entry["target_provider"] = codex_runtime["target_provider"]
            runtime_entry["target_base_url_source"] = codex_runtime["base_url_source"]
            runtime_entry["target_is_local"] = codex_runtime["is_local"]
            runtime_entry["target_kind"] = codex_runtime.get("target_kind")
            runtime_entry["target_provider_type"] = codex_runtime.get("target_provider_type")
            runtime_entry["remote_hub"] = bool(codex_runtime.get("remote_hub"))
            runtime_entry["instance_id"] = codex_runtime.get("instance_id")
            runtime_entry["max_hops"] = codex_runtime.get("max_hops")
            runtime_entry["api_key_configured"] = bool(codex_runtime["api_key"])
            runtime_entry["api_key_source"] = codex_runtime["api_key_source"]
            runtime_entry["prefer_lmstudio"] = codex_runtime["prefer_lmstudio"]
            runtime_entry["diagnostics"] = list(codex_runtime.get("diagnostics") or [])
        if name == "opencode":
            opencode_runtime = resolve_opencode_runtime_config()
            runtime_entry["target_base_url"] = opencode_runtime.get("base_url")
            runtime_entry["target_provider"] = opencode_runtime.get("target_provider")
            runtime_entry["target_base_url_source"] = opencode_runtime.get("base_url_source")
            runtime_entry["target_kind"] = opencode_runtime.get("target_kind")
            runtime_entry["target_provider_type"] = opencode_runtime.get("target_provider_type")
            runtime_entry["diagnostics"] = list(opencode_runtime.get("diagnostics") or [])
        data[name] = runtime_entry
    return data


def get_cli_backend_preflight(*, runtime_scope: str = "full") -> dict[str, dict]:
    from agent.common.sgpt_opencode import resolve_codex_runtime_config

    scope = str(runtime_scope or "full").strip().lower() or "full"
    worker_scope = scope in {"worker", "worker_only", "execution"}
    provider_urls = _get_runtime_provider_urls()
    lmstudio_base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    from agent.llm_integration import _normalize_ollama_base_url

    ollama_base_url = _normalize_ollama_base_url(provider_urls.get("ollama") or getattr(settings, "ollama_url", None))
    codex_runtime = resolve_codex_runtime_config()
    agent_cfg = _get_agent_config()

    cli_backends: dict[str, dict] = {}
    for name in sorted(SUPPORTED_CLI_BACKENDS):
        resolved = _resolve_backend_binary(name)
        cli_backends[name] = {
            "command": _configured_backend_command(name),
            "binary_path": resolved,
            "binary_available": bool(resolved),
            "install_hint": CLI_BACKEND_INSTALL_HINTS.get(name),
            "verify_command": CLI_BACKEND_VERIFY_COMMANDS.get(name),
        }

    lmstudio_probe = {
        "ok": False,
        "status": "not_configured" if not lmstudio_base_url else "unknown",
        "models_url": f"{lmstudio_base_url}/models" if lmstudio_base_url else None,
        "candidate_count": 0,
        "candidates": [],
    }
    if lmstudio_base_url and not worker_scope:
        from agent.llm_integration import probe_lmstudio_runtime

        try:
            lmstudio_probe = probe_lmstudio_runtime(
                lmstudio_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            lmstudio_probe = {
                "ok": False,
                "status": "error",
                "models_url": f"{lmstudio_base_url}/models",
                "candidate_count": 0,
                "candidates": [],
            }

    ollama_probe = {
        "ok": False,
        "status": "not_configured" if not ollama_base_url else "unknown",
        "tags_url": f"{ollama_base_url}/api/tags" if ollama_base_url else None,
        "candidate_count": 0,
        "models": [],
    }
    ollama_activity = {
        "ok": False,
        "status": "not_configured" if not ollama_base_url else "unknown",
        "ps_url": f"{ollama_base_url}/api/ps" if ollama_base_url else None,
        "active_count": 0,
        "gpu_active": False,
        "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
        "active_models": [],
    }
    if ollama_base_url and not worker_scope:
        from agent.llm_integration import probe_ollama_activity, probe_ollama_runtime

        try:
            ollama_probe = probe_ollama_runtime(
                ollama_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            ollama_probe = {
                "ok": False,
                "status": "error",
                "tags_url": f"{ollama_base_url}/api/tags",
                "candidate_count": 0,
                "models": [],
            }
        try:
            ollama_activity = probe_ollama_activity(
                ollama_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            ollama_activity = {
                "ok": False,
                "status": "error",
                "ps_url": f"{ollama_base_url}/api/ps",
                "active_count": 0,
                "gpu_active": False,
                "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
                "active_models": [],
            }

    local_provider_entries = []
    for backend in get_local_openai_backends(
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
        default_provider=_get_runtime_default_provider(),
        default_model=str(agent_cfg.get("default_model") or ""),
    ):
        local_provider_entries.append(
            {
                "provider": backend["provider"],
                "name": backend["name"],
                "base_url": backend.get("base_url"),
                "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                "transport_provider": backend.get("transport_provider"),
                "api_key_profile": backend.get("api_key_profile"),
                "provider_type": backend.get("provider_type") or "local_openai_compatible",
                "remote_hub": bool(backend.get("remote_hub")),
                "instance_id": backend.get("instance_id"),
                "max_hops": backend.get("max_hops"),
            }
        )

    return {
        "cli_backends": cli_backends,
        "research_backends": get_research_backend_preflight(),
        "providers": {
            "lmstudio": {
                "configured": bool(lmstudio_base_url),
                "base_url": lmstudio_base_url,
                "host_kind": _classify_runtime_target(lmstudio_base_url),
                "is_local": _is_probably_local_base_url(lmstudio_base_url),
                "status": lmstudio_probe.get("status"),
                "reachable": bool(lmstudio_probe.get("ok")),
                "models_url": lmstudio_probe.get("models_url"),
                "candidate_count": int(lmstudio_probe.get("candidate_count") or 0),
                "candidates": list(lmstudio_probe.get("candidates") or []),
                "runtime_scope": scope,
                "probe_skipped": bool(worker_scope),
            },
            "ollama": {
                "configured": bool(ollama_base_url),
                "base_url": ollama_base_url,
                "host_kind": _classify_runtime_target(ollama_base_url),
                "is_local": _is_probably_local_base_url(ollama_base_url),
                "status": ollama_probe.get("status"),
                "reachable": bool(ollama_probe.get("ok")),
                "tags_url": ollama_probe.get("tags_url"),
                "candidate_count": int(ollama_probe.get("candidate_count") or 0),
                "models": list(ollama_probe.get("models") or []),
                "activity": {
                    "status": ollama_activity.get("status"),
                    "reachable": bool(ollama_activity.get("ok")),
                    "ps_url": ollama_activity.get("ps_url"),
                    "active_count": int(ollama_activity.get("active_count") or 0),
                    "gpu_active": bool(ollama_activity.get("gpu_active")),
                    "executor_summary": dict(ollama_activity.get("executor_summary") or {"gpu": 0, "cpu": 0, "unknown": 0}),
                    "active_models": list(ollama_activity.get("active_models") or []),
                },
                "runtime_scope": scope,
                "probe_skipped": bool(worker_scope),
            },
            "codex": {
                "configured": bool(codex_runtime.get("base_url")),
                "base_url": codex_runtime.get("base_url"),
                "target_provider": codex_runtime.get("target_provider"),
                "base_url_source": codex_runtime.get("base_url_source"),
                "host_kind": _classify_runtime_target(codex_runtime.get("base_url")),
                "is_local": bool(codex_runtime.get("is_local")),
                "api_key_configured": bool(codex_runtime.get("api_key")),
                "api_key_source": codex_runtime.get("api_key_source"),
                "prefer_lmstudio": bool(codex_runtime.get("prefer_lmstudio")),
                "target_kind": codex_runtime.get("target_kind"),
                "target_provider_type": codex_runtime.get("target_provider_type"),
                "remote_hub": bool(codex_runtime.get("remote_hub")),
                "instance_id": codex_runtime.get("instance_id"),
                "max_hops": codex_runtime.get("max_hops"),
                "diagnostics": list(codex_runtime.get("diagnostics") or []),
            },
            "local_openai": local_provider_entries,
        },
    }


def get_cli_backend_capabilities() -> dict[str, dict]:
    return {k: dict(v) for k, v in CLI_BACKEND_CAPABILITIES.items()}


def _prioritize_code_backends(candidates: list[str]) -> list[str]:
    code_pref = ["ananta-worker", "sgpt", "codex", "aider", "opencode", "mistral_code", "deerflow", "ananta_research", "browser_use"]
    ordered = [c for c in code_pref if c in candidates]
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _split_cooldown_candidates(candidates: list[str], now: float) -> tuple[list[str], list[str]]:
    active: list[str] = []
    cooled: list[str] = []
    for candidate in candidates:
        until = float(_BACKEND_RUNTIME.get(candidate, {}).get("cooldown_until") or 0.0)
        if until > now and len(candidates) > 1:
            cooled.append(candidate)
        else:
            active.append(candidate)
    return active, cooled


def _choose_candidates(
    requested: str,
    prompt: str,
    routing_policy: dict | None = None,
) -> list[str]:
    policy = routing_policy or {}
    allowed = [b for b in (policy.get("allowed_backends") or []) if b in SUPPORTED_CLI_BACKENDS]
    if requested == "auto":
        preferred = (settings.sgpt_execution_backend or "ananta-worker").strip().lower()
        if preferred == "auto" or preferred not in SUPPORTED_CLI_BACKENDS:
            preferred = "ananta-worker"
        candidates = [preferred]
        for name in sorted(SUPPORTED_CLI_BACKENDS):
            if name not in candidates:
                candidates.append(name)
    else:
        candidates = [requested]

    if allowed:
        candidates = [c for c in candidates if c in allowed]

    p = (prompt or "").lower()
    code_like = any(k in p for k in ["refactor", "code", "patch", "test", "bug", "fix"])
    if code_like:
        candidates = _prioritize_code_backends(candidates)

    active, cooled = _split_cooldown_candidates(candidates, time.time())
    return active + cooled


def normalize_backend_flags(backend: str, options: list | None) -> tuple[list[str], list[str]]:
    """Gibt (valid_flags, rejected_flags) für das gewählte Backend zurück."""
    requested = backend.strip().lower()
    if requested not in CLI_BACKEND_CAPABILITIES:
        return [], options or []
    supported = set(CLI_BACKEND_CAPABILITIES[requested]["supported_flags"])
    valid = []
    rejected = []
    for opt in options or []:
        if opt in supported:
            valid.append(opt)
        else:
            rejected.append(opt)
    return valid, rejected
