from __future__ import annotations

import logging
import shutil
import sys
import time
from typing import Any

from agent.cli_backends.coding_agent_contract import FreeClass
from agent.cli_backends.coding_agent_profiles import CLI_PROFILES, EXISTING_DESCRIPTORS
from agent.cli_backends.helpers import (
    _classify_runtime_target,
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_openai_base_url,
)
from agent.config import settings
from agent.local_llm_backends import get_local_openai_backends
from agent.research_backend import (
    RESEARCH_BACKEND_PROVIDERS,
    get_research_backend_preflight,
    is_research_backend,
    resolve_research_backend_config,
)

log = logging.getLogger(__name__)

PROFILE_CLI_BACKENDS = frozenset({"qwen_code", "gemini_cli", "copilot_cli", "cline", "kilo_code"})
SUPPORTED_CLI_BACKENDS = {
    "sgpt",
    "ananta-worker",
    "codex",
    "opencode",
    "claude_code",
    "aider",
    "mistral_code",
    *PROFILE_CLI_BACKENDS,
    *RESEARCH_BACKEND_PROVIDERS,
}
CLI_BACKEND_INSTALL_HINTS = {
    "sgpt": "python -m pip install shell-gpt",
    "ananta-worker": "python -m pip install shell-gpt",
    "codex": "npm i -g @openai/codex",
    "opencode": "npm i -g opencode-ai",
    # CLA-001: Claude Code / Claude CLI install hint. The CLI is
    # shipped as a Node.js script (`npm i -g @anthropic-ai/claude-code`).
    # Network access is needed only at install and at login; nothing
    # inside Ananta reads ~/.claude/.
    "claude_code": "npm i -g @anthropic-ai/claude-code",
    "aider": "python -m pip install aider-chat",
    "mistral_code": "npm i -g mistral-code",
    "qwen_code": "npm install -g @qwen-code/qwen-code",
    "gemini_cli": "npm install -g @google/gemini-cli",
    "copilot_cli": "Install GitHub Copilot CLI from the official GitHub release channel.",
    "cline": "Install Cline CLI from the official Cline release channel.",
    "kilo_code": "npm install -g @kilocode/cli",
    "deerflow": "Clone deer-flow and configure research_backend.command plus research_backend.working_dir.",
    "ananta_research": (
        "Install or clone ananta_research and configure research_backend.command plus research_backend.working_dir."
    ),
}
CLI_BACKEND_VERIFY_COMMANDS = {
    "sgpt": "python -m sgpt --help",
    "ananta-worker": "python -m sgpt --help",
    "codex": "codex --help",
    "opencode": "opencode --help",
    "claude_code": "claude --version",
    "aider": "aider --help",
    "mistral_code": "mistral-code --help",
    "qwen_code": "qwen --version",
    "gemini_cli": "gemini --version",
    "copilot_cli": "copilot --version",
    "cline": "cline --version",
    "kilo_code": "kilo --version",
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
    "claude_code": {
        "display_name": "Claude Code CLI",
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
    **{
        backend_id: {
            "display_name": CLI_PROFILES[backend_id].descriptor.display_name,
            "supports_model": CLI_PROFILES[backend_id].model_flag is not None,
            "supports_model_selection": CLI_PROFILES[backend_id].model_flag is not None,
            "supported_flags": [],
            "supports_temperature": False,
            "supports_top_p": False,
            "integration_kind": CLI_PROFILES[backend_id].descriptor.integration_kind.value,
            "free_class": CLI_PROFILES[backend_id].descriptor.free_class.value,
            "capabilities": CLI_PROFILES[backend_id].descriptor.capabilities.as_dict(),
        }
        for backend_id in PROFILE_CLI_BACKENDS
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
    from agent.cli_backends.provisioning import resolve_provisioned_backend_binary

    if is_research_backend(backend):
        return resolve_research_backend_config(provider_override=backend).get("binary_path")
    if backend in {"sgpt", "ananta-worker"}:
        return sys.executable if sys.executable else None
    if backend == "codex":
        return shutil.which(settings.codex_path or "codex") or resolve_provisioned_backend_binary(backend)
    if backend == "opencode":
        return shutil.which(settings.opencode_path or "opencode")
    if backend == "claude_code":
        return shutil.which(
            getattr(settings, "claude_path", "claude") or "claude"
        ) or resolve_provisioned_backend_binary(backend)
    if backend == "aider":
        return shutil.which(settings.aider_path or "aider")
    if backend == "mistral_code":
        return shutil.which(settings.mistral_code_path or "mistral-code")
    if backend in PROFILE_CLI_BACKENDS:
        return shutil.which(CLI_PROFILES[backend].binary_name)
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
    if backend == "claude_code":
        return getattr(settings, "claude_path", "claude") or "claude"
    if backend == "aider":
        return settings.aider_path or "aider"
    if backend == "mistral_code":
        return settings.mistral_code_path or "mistral-code"
    if backend in PROFILE_CLI_BACKENDS:
        return CLI_PROFILES[backend].binary_name
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
    from agent.cli_backends.opencode import resolve_codex_runtime_config, resolve_opencode_runtime_config

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
        if name == "claude_code":
            from agent.cli_backends.opencode import resolve_claude_runtime_config

            claude_runtime = resolve_claude_runtime_config()
            runtime_entry["enabled"] = bool(claude_runtime.get("enabled"))
            runtime_entry["auth_mode"] = claude_runtime.get("auth_mode")
            runtime_entry["api_key_required"] = bool(claude_runtime.get("api_key_required"))
            runtime_entry["permission_mode"] = claude_runtime.get("permission_mode")
            runtime_entry["default_model"] = claude_runtime.get("default_model")
            runtime_entry["diagnostics"] = list(claude_runtime.get("diagnostics") or [])
        data[name] = runtime_entry
    return data


def _claude_login_command_for_mode(auth_mode: str | None) -> str | None:
    """CLA-002: liefert den offiziellen Claude CLI Login-Befehl fuer den
    gegebenen auth mode, oder None wenn kein manueller Login noetig ist.

    Wie bei codex ist der String nur ein UI-Hinweis; Ananta fuehrt ihn
    nicht aus und liest keine Dateien aus ~/.claude/.
    """
    mode = str(auth_mode or "").strip().lower()
    if mode == "claude_login":
        claude_path = str(getattr(settings, "claude_path", "claude") or "claude").strip() or "claude"
        return f"{claude_path} login"
    return None


def _codex_login_command_for_mode(auth_mode: str | None) -> str | None:
    """CCA-002: return the official Codex CLI login command for the
    given auth mode, or None if no manual login is required.

    The string is returned as a hint for the UI; Ananta does not
    execute it. The user runs it locally and the Codex CLI manages
    its own credentials under ~/.codex/.

    * "api_key" — Codex CLI does not require a separate login; the
      API key is supplied via ``OPENAI_API_KEY``. The hint is None.
    * "chatgpt_login" — the user must run ``codex login`` to
      authenticate against ChatGPT. The hint is the literal command.
    """
    mode = str(auth_mode or "").strip().lower()
    if mode == "chatgpt_login":
        codex_path = str(getattr(settings, "codex_path", "codex") or "codex").strip() or "codex"
        return f"{codex_path} login"
    return None


def get_cli_backend_preflight(*, runtime_scope: str = "full") -> dict[str, dict]:
    from agent.cli_backends.opencode import resolve_claude_runtime_config, resolve_codex_runtime_config

    scope = str(runtime_scope or "full").strip().lower() or "full"
    worker_scope = scope in {"worker", "worker_only", "execution"}
    provider_urls = _get_runtime_provider_urls()
    lmstudio_base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    from agent.llm_integration import _normalize_ollama_base_url

    ollama_base_url = _normalize_ollama_base_url(provider_urls.get("ollama") or getattr(settings, "ollama_url", None))
    codex_runtime = resolve_codex_runtime_config()
    claude_runtime = resolve_claude_runtime_config()
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
    # cliproxyapi-007: optional display labels for known provider
    # ids. Adding a label for a new id is purely additive — LM
    # Studio and Ollama preflight entries stay unchanged.
    _DISPLAY_LABELS = {"cliproxyapi": "CLI Proxy API"}
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
                "display_name": _DISPLAY_LABELS.get(backend["provider"]),
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
                    "executor_summary": dict(
                        ollama_activity.get("executor_summary") or {"gpu": 0, "cpu": 0, "unknown": 0}
                    ),
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
                # CCA-002: auth_mode + auth_status + login_command are
                # surfaced here so the Angular UI can show the current
                # auth state without re-deriving it.
                "auth_mode": codex_runtime.get("auth_mode", "api_key"),
                "api_key_required": bool(codex_runtime.get("api_key_required", True)),
                "login_command": _codex_login_command_for_mode(
                    codex_runtime.get("auth_mode", "api_key"),
                ),
            },
            # CLA-002: Claude Code CLI Health/Auth-Status. Kein
            # Token-File-Lesen: installed kommt aus shutil.which,
            # der Login-Status bleibt Sache des CLI selbst —
            # not_logged_in zeigt sich erst im Run und wird dann
            # als Fehlertext durchgereicht.
            "claude": {
                "enabled": bool(claude_runtime.get("enabled")),
                "installed": bool(_resolve_backend_binary("claude_code")),
                "binary_path": _resolve_backend_binary("claude_code"),
                "command": claude_runtime.get("command"),
                "auth_mode": claude_runtime.get("auth_mode", "claude_login"),
                "api_key_required": bool(claude_runtime.get("api_key_required", False)),
                "login_command": _claude_login_command_for_mode(
                    claude_runtime.get("auth_mode", "claude_login"),
                ),
                "default_model": claude_runtime.get("default_model"),
                "permission_mode": claude_runtime.get("permission_mode"),
                "timeout_seconds": claude_runtime.get("timeout_seconds"),
                "max_concurrent_runs": claude_runtime.get("max_concurrent_runs"),
                "install_hint": CLI_BACKEND_INSTALL_HINTS.get("claude_code"),
                "diagnostics": list(claude_runtime.get("diagnostics") or []),
            },
            "local_openai": local_provider_entries,
        },
    }


def diagnose_cli_backend(backend: str, *, timeout: float = 15.0) -> dict:
    """COMMON-003: nicht-mutierende Diagnose eines CLI-Backends.

    Prueft Binary-Aufloesung und fuehrt den statischen Verify-Befehl
    (z.B. ``claude --version``) als Argumentliste aus — kein shell=True,
    kein Netzwerkzwang, keine Token-Dateien. Ausgaben werden gekuerzt.
    """
    import shlex
    import subprocess

    name = str(backend or "").strip().lower()
    if name not in SUPPORTED_CLI_BACKENDS:
        return {"backend": name, "status": "unsupported"}
    binary = _resolve_backend_binary(name)
    result: dict[str, Any] = {
        "backend": name,
        "command": _configured_backend_command(name),
        "binary_path": binary,
        "binary_available": bool(binary),
        "install_hint": CLI_BACKEND_INSTALL_HINTS.get(name),
        "verify_command": CLI_BACKEND_VERIFY_COMMANDS.get(name),
        "status": "not_installed",
        "version_probe": None,
    }
    if not binary:
        return result
    verify = str(CLI_BACKEND_VERIFY_COMMANDS.get(name) or "")
    probe_args = shlex.split(verify)[1:] if verify else ["--version"]
    try:
        proc = subprocess.run(  # noqa: S603 - binary via shutil.which, args from static verify table
            [binary, *probe_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        result["version_probe"] = {
            "rc": proc.returncode,
            "stdout": (proc.stdout or "")[:2000],
            "stderr": (proc.stderr or "")[:2000],
        }
        result["status"] = "ready" if proc.returncode == 0 else "error"
    except subprocess.TimeoutExpired:
        result["version_probe"] = {"rc": -1, "stdout": "", "stderr": "timeout"}
        result["status"] = "timeout"
    except Exception as exc:  # pragma: no cover - defensive
        result["version_probe"] = {"rc": -1, "stdout": "", "stderr": str(exc)[:500]}
        result["status"] = "error"
    return result


def get_cli_backend_capabilities() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for backend_id, raw in CLI_BACKEND_CAPABILITIES.items():
        item = dict(raw)
        item.setdefault("supports_model_selection", bool(item.get("supports_model")))
        item.setdefault("supported_options", list(item.get("supported_flags") or []))
        item.setdefault("install_hint", CLI_BACKEND_INSTALL_HINTS.get(backend_id))
        item["available"] = bool(_resolve_backend_binary(backend_id))
        result[backend_id] = item
    return result


def _prioritize_code_backends(candidates: list[str]) -> list[str]:
    code_pref = [
        "ananta-worker",
        "sgpt",
        "codex",
        "claude_code",
        "qwen_code",
        "aider",
        "opencode",
        "gemini_cli",
        "copilot_cli",
        "cline",
        "kilo_code",
        "mistral_code",
        "deerflow",
        "ananta_research",
        "browser_use",
    ]
    ordered = [c for c in code_pref if c in candidates]
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _apply_coding_agent_cost_policy(candidates: list[str], policy: dict) -> list[str]:
    if not bool(policy.get("coding_agent_free_first", True)):
        return candidates
    allow_paid = bool(policy.get("allow_paid_coding_agent_fallback", False))
    descriptors = {
        **{backend_id: profile.descriptor for backend_id, profile in CLI_PROFILES.items()},
        **EXISTING_DESCRIPTORS,
    }
    original_index = {backend_id: index for index, backend_id in enumerate(candidates)}
    free_rank = {
        FreeClass.INCLUDED_FREE_INFERENCE: 0,
        FreeClass.FREE_TIER_LIMITED: 1,
        FreeClass.OPEN_SOURCE_BYOK: 2,
        FreeClass.PAID_OR_UNKNOWN: 3,
    }
    preferred = candidates[:1]
    fallback = candidates[1:]
    eligible: list[str] = []
    for backend_id in fallback:
        descriptor = descriptors.get(backend_id)
        if descriptor is not None and descriptor.free_class is FreeClass.PAID_OR_UNKNOWN and not allow_paid:
            continue
        if str(_BACKEND_RUNTIME.get(backend_id, {}).get("quota_state") or "") == "exhausted":
            continue
        eligible.append(backend_id)
    eligible.sort(
        key=lambda backend_id: (
            free_rank.get(
                descriptors[backend_id].free_class if backend_id in descriptors else FreeClass.PAID_OR_UNKNOWN,
                4,
            ),
            original_index[backend_id],
        )
    )
    return preferred + eligible


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
        # COMMON-002: claude_code ist strikt opt-in. Im auto-Modus wird
        # es nur als Kandidat gefuehrt, wenn claude_cli.enabled=true und
        # das Binary installiert ist. Explizite Anforderung bleibt
        # erlaubt — dort liefert run_claude_command eine klare Diagnose.
        if "claude_code" in candidates:
            from agent.cli_backends.opencode import resolve_claude_runtime_config

            claude_ready = bool(resolve_claude_runtime_config().get("enabled")) and bool(
                _resolve_backend_binary("claude_code")
            )
            if not claude_ready:
                candidates = [c for c in candidates if c != "claude_code"]
    else:
        candidates = [requested]

    if allowed:
        candidates = [c for c in candidates if c in allowed]

    p = (prompt or "").lower()
    code_like = any(k in p for k in ["refactor", "code", "patch", "test", "bug", "fix"])
    if code_like:
        candidates = _prioritize_code_backends(candidates)
    if requested == "auto":
        candidates = _apply_coding_agent_cost_policy(candidates, policy)

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
