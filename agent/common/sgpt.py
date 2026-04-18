import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile
import time

from flask import current_app, has_app_context

from agent.config import settings
from agent.llm_integration import (
    _find_matching_lmstudio_candidate,
    _find_matching_ollama_candidate,
    probe_lmstudio_runtime,
    probe_ollama_runtime,
    resolve_ollama_model,
)
from agent.local_llm_backends import get_local_openai_backends, resolve_local_openai_backend
from agent.model_selection import normalize_legacy_model_name
from agent.research_backend import (
    RESEARCH_BACKEND_PROVIDERS,
    get_research_backend_preflight,
    is_research_backend,
    resolve_research_backend_config,
    run_research_backend_command,
)

sgpt_lock = threading.Lock()

SUPPORTED_CLI_BACKENDS = {"sgpt", "codex", "opencode", "aider", "mistral_code", *RESEARCH_BACKEND_PROVIDERS}
CLI_BACKEND_INSTALL_HINTS = {
    "sgpt": "python -m pip install shell-gpt",
    "codex": "npm i -g @openai/codex",
    "opencode": "npm i -g opencode-ai",
    "aider": "python -m pip install aider-chat",
    "mistral_code": "npm i -g mistral-code",
    "deerflow": "Clone deer-flow and configure research_backend.command plus research_backend.working_dir.",
    "ananta_research": "Install or clone ananta_research and configure research_backend.command plus research_backend.working_dir.",
}
CLI_BACKEND_VERIFY_COMMANDS = {
    "sgpt": "python -m sgpt --help",
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
    if backend == "codex":
        return shutil.which(settings.codex_path or "codex")
    if backend == "opencode":
        return shutil.which(settings.opencode_path or "opencode")
    if backend == "aider":
        return shutil.which(settings.aider_path or "aider")
    if backend == "mistral_code":
        return shutil.which(settings.mistral_code_path or "mistral-code")
    # sgpt is a python module; we treat python executable as available indicator.
    return sys.executable if sys.executable else None


def _configured_backend_command(backend: str) -> str:
    if is_research_backend(backend):
        return str(resolve_research_backend_config(provider_override=backend).get("command") or "")
    if backend == "codex":
        return settings.codex_path or "codex"
    if backend == "opencode":
        return settings.opencode_path or "opencode"
    if backend == "aider":
        return settings.aider_path or "aider"
    if backend == "mistral_code":
        return settings.mistral_code_path or "mistral-code"
    return f"{sys.executable} -m sgpt" if sys.executable else "python -m sgpt"


def _classify_runtime_target(url: str | None) -> str | None:
    raw = (url or "").strip().lower()
    if not raw:
        return None
    if "host.docker.internal" in raw:
        return "docker_host"
    if "localhost" in raw or "127.0.0.1" in raw:
        return "loopback"
    if any(marker in raw for marker in ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        return "private_network"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "remote"
    return "unknown"


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


def get_cli_backend_preflight() -> dict[str, dict]:
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
    if lmstudio_base_url:
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
    if ollama_base_url:
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
    code_pref = ["codex", "aider", "opencode", "mistral_code", "sgpt", "deerflow", "ananta_research"]
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
        preferred = (settings.sgpt_execution_backend or "sgpt").strip().lower()
        if preferred == "auto" or preferred not in SUPPORTED_CLI_BACKENDS:
            preferred = "sgpt"
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
    """
    Gibt (valid_flags, rejected_flags) für das gewählte Backend zurück.
    """
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


def run_sgpt_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    model: str | None = None,
) -> tuple[int, str, str]:
    """
    Führt einen SGPT-Befehl zentral aus, inkl. korrekter Environment-Injektion.
    Gibt (returncode, stdout, stderr) zurück.
    """
    options = options or []
    if "--no-interaction" not in options:
        options.append("--no-interaction")

    # Modell aus Settings nutzen, falls nicht explizit angegeben
    selected_model = model or settings.sgpt_default_model
    args = ["--model", selected_model] + options + [prompt]

    with sgpt_lock:
        env = os.environ.copy()

        # LMStudio Integration
        lmstudio_url = settings.lmstudio_url
        if lmstudio_url:
            if "/v1" in lmstudio_url:
                base_url = lmstudio_url.split("/v1")[0] + "/v1"
            else:
                base_url = lmstudio_url
            env["OPENAI_API_BASE"] = base_url

        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = "sk-no-key-needed"

        try:
            logging.info(f"Zentraler SGPT-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - args are constructed in-process; no shell=True
                [sys.executable, "-m", "sgpt"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("SGPT Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"SGPT Fehler: {e}")
            return -1, "", str(e)


def run_opencode_command(
    prompt: str,
    model: str | None = None,
    timeout: int = 60,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """
    Führt einen OpenCode-CLI-Aufruf aus.
    Gibt (returncode, stdout, stderr) zurück.
    """
    opencode_bin = settings.opencode_path or "opencode"
    opencode_resolved = shutil.which(opencode_bin)
    if opencode_resolved is None:
        return -1, "", (f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai")

    session_meta = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
    runtime_meta = (
        session_meta.get("opencode_runtime")
        if isinstance(session_meta.get("opencode_runtime"), dict)
        else {}
    )
    if session and str(runtime_meta.get("kind") or "").strip().lower() == "native_server":
        from agent.services.opencode_runtime_service import get_opencode_runtime_service

        return get_opencode_runtime_service().run_session_turn(
            session,
            prompt=prompt,
            timeout=timeout,
            model=model,
        )
    opencode_execution_mode = str(session_meta.get("opencode_execution_mode") or "").strip().lower()
    if session and opencode_execution_mode == "live_terminal":
        from agent.services.live_terminal_session_service import get_live_terminal_session_service

        return get_live_terminal_session_service().run_opencode_turn(
            session,
            prompt=prompt,
            timeout=timeout,
            model=model,
            workdir=workdir,
        )
    if session and opencode_execution_mode == "interactive_terminal":
        from agent.services.live_terminal_session_service import get_live_terminal_session_service

        terminal_service = get_live_terminal_session_service()
        session_info = terminal_service.ensure_session_for_cli(session, workdir=workdir) or {}
        rc, out, err, command_label = _run_opencode_subprocess(
            prompt=prompt,
            model=model,
            timeout=timeout,
            workdir=workdir,
            output_format=None,
        )
        terminal_session_id = str(session_info.get("terminal_session_id") or session.get("id") or "").strip()
        if terminal_session_id:
            terminal_service.append_output(terminal_session_id, f"$ {command_label}\n")
            if out:
                terminal_service.append_output(terminal_session_id, f"{out}\n")
            if err:
                terminal_service.append_output(terminal_session_id, f"{err}\n")
        return rc, out, err
    rc, out, err, _ = _run_opencode_subprocess(
        prompt=prompt,
        model=model,
        timeout=timeout,
        workdir=workdir,
        output_format=None,
    )
    return rc, out, err


def _run_opencode_subprocess(
    *,
    prompt: str,
    model: str | None,
    timeout: int,
    workdir: str | None,
    output_format: str | None,
) -> tuple[int, str, str, str]:
    opencode_bin = settings.opencode_path or "opencode"
    opencode_resolved = shutil.which(opencode_bin)
    if opencode_resolved is None:
        hint = f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai"
        return -1, "", hint, opencode_bin

    with sgpt_lock:
        env = os.environ.copy()
        runtime_cfg = resolve_opencode_runtime_config(model=model)
        args = [opencode_resolved, "run"]
        selected_model = str(runtime_cfg.get("model") or "").strip()
        if selected_model:
            args.extend(["--model", selected_model])
        if output_format:
            args.extend(["--format", str(output_format)])
        try:
            diagnostics = list(runtime_cfg.get("diagnostics") or [])
            if diagnostics:
                logging.warning("OpenCode runtime diagnostics: %s", ",".join(diagnostics))
            temp_dir_ctx = tempfile.TemporaryDirectory() if runtime_cfg.get("provider_config") else contextlib.nullcontext(None)
            with temp_dir_ctx as tmp_dir:
                if runtime_cfg.get("provider_config") and tmp_dir:
                    config_dir = os.path.join(tmp_dir, "opencode")
                    os.makedirs(config_dir, exist_ok=True)
                    config_path = os.path.join(config_dir, "config.json")
                    with open(config_path, "w", encoding="utf-8") as handle:
                        json.dump(runtime_cfg["provider_config"], handle, ensure_ascii=True)
                    env["XDG_CONFIG_HOME"] = tmp_dir
                    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(runtime_cfg["provider_config"], ensure_ascii=True)
                env_prefix = " ".join(
                    f"{key}={shlex.quote(value)}" for key, value in env.items() if key in {"XDG_CONFIG_HOME"}
                )
                visible_command = " ".join(
                    [segment for segment in [env_prefix.strip(), " ".join(shlex.quote(part) for part in args)] if segment]
                )
                logging.info(f"Zentraler OpenCode-Aufruf: {args}")
                result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=timeout,
                    cwd=workdir or None,
                    input=str(prompt or ""),
                )
                return result.returncode, result.stdout, result.stderr, visible_command
        except subprocess.TimeoutExpired:
            logging.error("OpenCode Timeout")
            return -1, "", "Timeout", " ".join(shlex.quote(part) for part in args)
        except Exception as e:
            logging.exception(f"OpenCode Fehler: {e}")
            return -1, "", str(e), " ".join(shlex.quote(part) for part in args)


def _resolve_openai_compatible_base_url() -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    provider = _get_runtime_default_provider()
    provider_urls = _get_runtime_provider_urls()
    if provider == "lmstudio":
        return _normalize_lmstudio_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    elif provider in {"openai", "codex"}:
        raw_url = provider_urls.get("openai") or provider_urls.get("codex") or settings.openai_url
    else:
        raw_url = (
            provider_urls.get("openai")
            or provider_urls.get("codex")
            or provider_urls.get("lmstudio")
            or settings.openai_url
            or settings.lmstudio_url
        )

    if not raw_url:
        return None

    normalized = raw_url.strip()
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _normalize_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    normalized_lmstudio = _normalize_lmstudio_base_url(raw_url)
    if normalized_lmstudio:
        return normalized_lmstudio
    normalized = raw_url
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _get_agent_config() -> dict:
    if has_app_context():
        return (current_app.config.get("AGENT_CONFIG", {}) or {})
    return {}


def _get_runtime_provider_urls() -> dict:
    defaults = {
        "ollama": getattr(settings, "ollama_url", None),
        "lmstudio": getattr(settings, "lmstudio_url", None),
        "openai": getattr(settings, "openai_url", None),
        "anthropic": getattr(settings, "anthropic_url", None),
        "mock": getattr(settings, "mock_url", None),
    }
    if not has_app_context():
        return defaults
    configured = current_app.config.get("PROVIDER_URLS", {}) or {}
    if not isinstance(configured, dict):
        return defaults
    return {
        **defaults,
        **{key: value for key, value in configured.items() if value},
    }


def _get_runtime_default_provider() -> str:
    agent_cfg = _get_agent_config()
    return str(agent_cfg.get("default_provider") or settings.default_provider or "").strip().lower()


def _resolve_profile_api_key(profile_name: str | None) -> str | None:
    profile_name = str(profile_name or "").strip()
    if not profile_name:
        return None
    agent_cfg = _get_agent_config()
    profiles = agent_cfg.get("llm_api_key_profiles") or {}
    if not isinstance(profiles, dict):
        return None
    selected = profiles.get(profile_name)
    if isinstance(selected, str):
        return selected.strip() or None
    if isinstance(selected, dict):
        return str(selected.get("api_key") or "").strip() or None
    return None


def _is_probably_local_base_url(url: str | None) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return False
    local_markers = (
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "192.168.",
        "10.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
    )
    return any(marker in raw for marker in local_markers)


def _build_codex_runtime_diagnostics(*, base_url: str | None, api_key: str | None, is_local: bool) -> list[str]:
    diagnostics: list[str] = []
    if not base_url:
        diagnostics.append("codex_runtime_missing_base_url")
    if not api_key and not is_local:
        diagnostics.append("codex_runtime_missing_api_key_for_remote_target")
    if base_url and _classify_runtime_target(base_url) == "unknown":
        diagnostics.append("codex_runtime_target_host_kind_unknown")
    return diagnostics


def _split_cli_model_identifier(model: str | None) -> tuple[str | None, str | None]:
    raw = str(model or "").strip()
    if not raw:
        return None, None
    if "/" not in raw:
        return None, raw
    provider_name, model_name = raw.split("/", 1)
    provider_name = provider_name.strip().lower() or None
    model_name = model_name.strip() or None
    return provider_name, model_name


def _infer_local_opencode_target(
    model: str | None,
    *,
    provider_urls: dict[str, object],
    preferred_provider: str | None,
    timeout: int,
) -> tuple[str | None, str | None]:
    raw_model = str(model or "").strip()
    if not raw_model:
        return None, None
    provider_hint = str(preferred_provider or "").strip().lower()
    local_candidates = [provider_hint] if provider_hint in {"ollama", "lmstudio"} else []
    for candidate in ("ollama", "lmstudio"):
        if candidate not in local_candidates:
            local_candidates.append(candidate)

    for candidate in local_candidates:
        if candidate == "ollama":
            base_url = _normalize_ollama_openai_base_url(str(provider_urls.get("ollama") or "").strip())
            if not base_url:
                continue
            try:
                probe = probe_ollama_runtime(base_url, timeout=timeout)
            except Exception:
                continue
            matched = _find_matching_ollama_candidate(raw_model, list(probe.get("models") or [])) if isinstance(probe, dict) else None
            if matched:
                resolved_model = resolve_ollama_model(raw_model, base_url, timeout=timeout) or raw_model
                return "ollama", str(resolved_model).strip() or raw_model
        elif candidate == "lmstudio":
            base_url = str(provider_urls.get("lmstudio") or "").strip()
            if not base_url:
                continue
            try:
                probe = probe_lmstudio_runtime(base_url, timeout=timeout)
            except Exception:
                continue
            matched = _find_matching_lmstudio_candidate(raw_model, list(probe.get("candidates") or [])) if isinstance(probe, dict) else None
            if matched:
                resolved_model = str((matched or {}).get("id") or "").strip() or raw_model
                return "lmstudio", resolved_model
    return None, None


def _normalize_ollama_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_ollama_base_url

    normalized = _normalize_ollama_base_url(url)
    if not normalized:
        return None
    normalized = normalized.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _build_opencode_runtime_diagnostics(*, base_url: str | None) -> list[str]:
    diagnostics: list[str] = []
    if not base_url:
        diagnostics.append("opencode_runtime_missing_base_url")
    elif _classify_runtime_target(base_url) == "unknown":
        diagnostics.append("opencode_runtime_target_host_kind_unknown")
    return diagnostics


def _build_opencode_theless_agent_config() -> dict[str, object]:
    return {
        "description": "Toolless worker for structured JSON replies",
        "prompt": "Return concise structured answers. Never call tools.",
        "tools": {
            "bash": False,
            "read": False,
            "glob": False,
            "grep": False,
            "edit": False,
            "write": False,
            "task": False,
            "webfetch": False,
            "todowrite": False,
            "question": False,
            "skill": False,
        },
    }


def _normalize_opencode_tool_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"toolless", "readonly", "full"}:
        return mode
    return "full"


def _normalize_opencode_execution_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"backend", "live_terminal", "interactive_terminal"}:
        return mode
    return "live_terminal"


def resolve_opencode_runtime_config(model: str | None = None) -> dict[str, object]:
    agent_cfg = _get_agent_config()
    provider_urls = _get_runtime_provider_urls()
    opencode_runtime_cfg = agent_cfg.get("opencode_runtime") if isinstance(agent_cfg.get("opencode_runtime"), dict) else {}
    tool_mode = _normalize_opencode_tool_mode(opencode_runtime_cfg.get("tool_mode"))
    execution_mode = _normalize_opencode_execution_mode(opencode_runtime_cfg.get("execution_mode"))
    forced_target_provider = str(opencode_runtime_cfg.get("target_provider") or "").strip().lower() or None
    if forced_target_provider not in {"ollama", "lmstudio"}:
        forced_target_provider = None
    configured_default_model = (
        str(agent_cfg.get("opencode_default_model") or "").strip()
        or str(agent_cfg.get("default_model") or agent_cfg.get("model") or "").strip()
        or str(settings.opencode_default_model or "").strip()
    )
    default_provider = str(agent_cfg.get("default_provider") or _get_runtime_default_provider() or "").strip() or None
    raw_model = normalize_legacy_model_name(
        str(model or configured_default_model or "").strip() or None,
        provider=forced_target_provider or default_provider,
    )
    explicit_provider, explicit_model = _split_cli_model_identifier(raw_model)
    if forced_target_provider and explicit_provider in {"ollama", "lmstudio"} and explicit_provider != forced_target_provider:
        raw_model = str(explicit_model or raw_model or "").strip() or None
        explicit_provider = None
        explicit_model = None
    inference_timeout = max(1, min(int(getattr(settings, "http_timeout", 120) or 120), 5))
    inferred_provider, inferred_model = (None, None)
    if not explicit_provider and raw_model:
        inferred_provider, inferred_model = _infer_local_opencode_target(
            raw_model,
            provider_urls=provider_urls,
            preferred_provider=forced_target_provider or default_provider or _get_runtime_default_provider(),
            timeout=inference_timeout,
        )
    built_in_providers = {
        "opencode",
        "openai",
        "anthropic",
        "gemini",
        "groq",
        "openrouter",
        "bedrock",
        "azure",
        "vertexai",
        "copilot",
        "lmstudio",
    }

    target_provider = explicit_provider or forced_target_provider or inferred_provider or default_provider
    target_model = explicit_model if explicit_provider else (inferred_model or raw_model)
    target_model = normalize_legacy_model_name(target_model, provider=target_provider)
    base_url = None
    base_url_source = None
    target_provider_type = None
    target_kind = None
    local_target = None

    if target_provider == "ollama":
        if tool_mode != "toolless":
            tool_mode = "toolless"
        base_url = _normalize_ollama_openai_base_url(provider_urls.get("ollama") or getattr(settings, "ollama_url", None))
        base_url_source = "ollama_url"
        target_provider_type = "local_openai_compatible"
        target_kind = "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
        if target_model and base_url:
            try:
                resolve_timeout = float(getattr(settings, "http_timeout", 120) or 120)
            except (TypeError, ValueError):
                resolve_timeout = 120.0
            target_model = resolve_ollama_model(target_model, base_url, timeout=min(resolve_timeout, 10.0))
    elif target_provider == "lmstudio":
        base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or getattr(settings, "lmstudio_url", None))
        base_url_source = "lmstudio_url"
        target_provider_type = "local_openai_compatible"
        target_kind = "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
    elif target_provider and target_provider not in built_in_providers:
        local_target = resolve_local_openai_backend(
            target_provider,
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=_get_runtime_default_provider(),
            default_model=str(agent_cfg.get("default_model") or ""),
        )
        if local_target and local_target.get("base_url"):
            base_url = _normalize_openai_base_url(local_target.get("base_url"))
            base_url_source = f"local_openai.{target_provider}"
            target_provider_type = str(local_target.get("provider_type") or "local_openai_compatible")
            target_kind = "remote_ananta_hub" if bool(local_target.get("remote_hub")) else (
                "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
            )

    provider_config = None
    cli_model = raw_model
    if target_provider and target_model and base_url:
        provider_entry = {
            "npm": "@ai-sdk/openai-compatible",
            "models": {str(target_model): {}},
            "options": {"baseURL": base_url},
        }
        provider_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {str(target_provider): provider_entry},
            "agent": {},
            "mode": {},
            "plugin": [],
            "command": {},
        }
        provider_config["model"] = f"{target_provider}/{target_model}"
        provider_config["small_model"] = f"{target_provider}/{target_model}"
        if target_provider == "ollama" and tool_mode == "toolless":
            provider_config["agent"]["ananta-worker"] = _build_opencode_theless_agent_config()
            provider_config["default_agent"] = "ananta-worker"
        cli_model = f"{target_provider}/{target_model}"

    diagnostics = _build_opencode_runtime_diagnostics(base_url=base_url) if (target_provider in {"ollama", "lmstudio"} or local_target) else []
    return {
        "model": cli_model,
        "target_provider": target_provider,
        "target_model": target_model,
        "base_url": base_url,
        "base_url_source": base_url_source,
        "target_kind": target_kind,
        "target_provider_type": target_provider_type,
        "tool_mode": tool_mode,
        "execution_mode": execution_mode,
        "provider_config": provider_config,
        "diagnostics": diagnostics,
    }


def resolve_codex_runtime_config() -> dict:
    agent_cfg = _get_agent_config()
    provider_urls = _get_runtime_provider_urls()
    codex_cfg = agent_cfg.get("codex_cli") or {}
    if not isinstance(codex_cfg, dict):
        codex_cfg = {}

    explicit_base_url = _normalize_openai_base_url(codex_cfg.get("base_url"))
    prefer_lmstudio = codex_cfg.get("prefer_lmstudio")
    target_provider = str(codex_cfg.get("target_provider") or "").strip().lower() or None
    if prefer_lmstudio is None:
        prefer_lmstudio = _get_runtime_default_provider() == "lmstudio"
    local_target = resolve_local_openai_backend(target_provider, agent_cfg=agent_cfg, provider_urls=provider_urls) if target_provider else None

    if explicit_base_url:
        base_url = explicit_base_url
        base_url_source = "codex_cli.base_url"
    elif local_target and local_target.get("base_url"):
        base_url = _normalize_openai_base_url(local_target.get("base_url"))
        base_url_source = f"codex_cli.target_provider:{local_target['provider']}"
    elif prefer_lmstudio:
        base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
        base_url_source = "lmstudio_url"
    else:
        base_url = _resolve_openai_compatible_base_url()
        base_url_source = "default_provider"

    api_key = str(codex_cfg.get("api_key") or "").strip() or None
    api_key_source = "codex_cli.api_key" if api_key else None
    if not api_key:
        api_key = _resolve_profile_api_key(codex_cfg.get("api_key_profile"))
        if api_key:
            api_key_source = "codex_cli.api_key_profile"
    if not api_key and local_target:
        api_key = str(local_target.get("api_key") or "").strip() or None
        if api_key:
            api_key_source = f"local_openai.{local_target['provider']}"
        elif local_target.get("api_key_profile"):
            api_key = _resolve_profile_api_key(local_target.get("api_key_profile"))
            if api_key:
                api_key_source = f"local_openai.{local_target['provider']}.api_key_profile"
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key
        if api_key:
            api_key_source = "openai_api_key"
    if not api_key and _is_probably_local_base_url(base_url):
        api_key = "sk-no-key-needed"
        api_key_source = "local_dummy"
    is_local = _is_probably_local_base_url(base_url)
    target_kind = "local_openai"
    if local_target and bool(local_target.get("remote_hub")):
        target_kind = "remote_ananta_hub"
    elif not is_local:
        target_kind = "remote_openai_compatible"
    diagnostics = _build_codex_runtime_diagnostics(base_url=base_url, api_key=api_key, is_local=is_local)
    return {
        "base_url": base_url,
        "api_key": api_key,
        "target_provider": target_provider or ("lmstudio" if prefer_lmstudio else None),
        "base_url_source": base_url_source if base_url else None,
        "api_key_source": api_key_source,
        "is_local": is_local,
        "prefer_lmstudio": bool(prefer_lmstudio),
        "target_kind": target_kind,
        "target_provider_type": (local_target or {}).get("provider_type") if isinstance(local_target, dict) else None,
        "remote_hub": bool((local_target or {}).get("remote_hub")) if isinstance(local_target, dict) else False,
        "instance_id": (local_target or {}).get("instance_id") if isinstance(local_target, dict) else None,
        "max_hops": (local_target or {}).get("max_hops") if isinstance(local_target, dict) else None,
        "diagnostics": diagnostics,
    }


def run_codex_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Fuehrt einen OpenAI Codex CLI exec-Aufruf aus.

    Der Backend-Pfad ist explizit opt-in und setzt fuer lokale OpenAI-kompatible
    Runtimes (z. B. LM Studio) sowohl OPENAI_BASE_URL als auch OPENAI_API_BASE.
    """
    codex_bin = settings.codex_path or "codex"
    codex_resolved = shutil.which(codex_bin)
    if codex_resolved is None:
        return -1, "", (f"Codex binary '{codex_bin}' not found. Install with: npm i -g @openai/codex")

    args = [codex_resolved, "exec", "--skip-git-repo-check"]
    selected_model = model or settings.codex_default_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append(prompt)

    with sgpt_lock:
        env = os.environ.copy()
        runtime_cfg = resolve_codex_runtime_config()
        base_url = runtime_cfg["base_url"]
        api_key = runtime_cfg["api_key"]
        diagnostics = list(runtime_cfg.get("diagnostics") or [])
        if not base_url:
            return -1, "", "Codex runtime target is not configured: missing OpenAI-compatible base_url"
        if not api_key and not bool(runtime_cfg.get("is_local")):
            return -1, "", "Codex runtime target requires API key for remote endpoint"
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            env["OPENAI_API_BASE"] = base_url

        if api_key:
            env["OPENAI_API_KEY"] = api_key
        if diagnostics:
            logging.warning("Codex runtime diagnostics: %s", ",".join(diagnostics))

        try:
            logging.info(f"Zentraler Codex-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Codex Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Codex Fehler: {e}")
            return -1, "", str(e)


def run_aider_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Führt einen Aider-CLI-Aufruf aus (non-interactive).
    """
    aider_bin = settings.aider_path or "aider"
    aider_resolved = shutil.which(aider_bin)
    if aider_resolved is None:
        return -1, "", (f"Aider binary '{aider_bin}' not found. Install with: pip install aider-chat")

    args = [aider_resolved, "--message", prompt, "--yes-always"]
    selected_model = model or settings.aider_default_model
    if selected_model:
        args.extend(["--model", selected_model])

    with sgpt_lock:
        env = os.environ.copy()
        try:
            logging.info(f"Zentraler Aider-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Aider Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Aider Fehler: {e}")
            return -1, "", str(e)


def run_mistral_code_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Führt einen Mistral-Code-CLI-Aufruf aus.
    """
    mistral_bin = settings.mistral_code_path or "mistral-code"
    mistral_resolved = shutil.which(mistral_bin)
    if mistral_resolved is None:
        return -1, "", (f"Mistral Code binary '{mistral_bin}' not found. Install with: npm i -g mistral-code")

    args = [mistral_resolved]

    with sgpt_lock:
        env = os.environ.copy()
        if not env.get("MISTRAL_API_KEY") and getattr(settings, "mistral_api_key", None):
            env["MISTRAL_API_KEY"] = settings.mistral_api_key
        try:
            logging.info(f"Zentraler Mistral-Code-Aufruf: {args}")
            input_lines = [prompt]
            if model or settings.mistral_code_default_model:
                input_lines.append(f"/model {(model or settings.mistral_code_default_model)}")
            input_lines.append("exit")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                input="\n".join(input_lines) + "\n",
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Mistral Code Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Mistral Code Fehler: {e}")
            return -1, "", str(e)


def run_llm_cli_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    backend: str = "sgpt",
    model: str | None = None,
    temperature: float | None = None,
    routing_policy: dict | None = None,
    research_context: dict | None = None,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str, str]:
    """
    Führt den konfigurierten CLI-Backend-Aufruf aus.
    Rückgabe: (returncode, stdout, stderr, backend_used)
    """
    requested = (backend or "sgpt").strip().lower()
    candidates = _choose_candidates(requested=requested, prompt=prompt, routing_policy=routing_policy)

    last_error = ""
    now = time.time()
    for name in candidates:
        started = time.time()
        if name == "sgpt":
            rc, out, err = run_sgpt_command(prompt=prompt, options=options or [], timeout=timeout, model=model)
        elif name == "codex":
            rc, out, err = run_codex_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "opencode":
            rc, out, err = run_opencode_command(
                prompt=prompt,
                model=model,
                timeout=timeout,
                session=session,
                workdir=workdir,
            )
        elif name == "aider":
            rc, out, err = run_aider_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "mistral_code":
            rc, out, err = run_mistral_code_command(prompt=prompt, model=model, timeout=timeout)
        elif is_research_backend(name):
            rc, out, err = run_research_backend_command(
                prompt=prompt,
                model=model,
                temperature=temperature,
                timeout=timeout,
                provider=name,
                research_context=research_context,
            )
        else:
            continue

        rt = _BACKEND_RUNTIME.setdefault(name, {})
        rt["last_rc"] = rc
        rt["last_latency_ms"] = int((time.time() - started) * 1000)
        if rc == 0 or out:
            rt["last_success_at"] = now
            rt["consecutive_failures"] = 0
            rt["cooldown_until"] = 0.0
            rt["total_success"] = int(rt.get("total_success", 0)) + 1
            rt["last_error"] = ""
            return rc, out, err, name
        rt["last_failure_at"] = now
        rt["consecutive_failures"] = int(rt.get("consecutive_failures", 0)) + 1
        rt["total_failures"] = int(rt.get("total_failures", 0)) + 1
        rt["last_error"] = err or f"{name} failed with exit code {rc}"
        # Adaptive cooldown to prevent immediate repeated failures.
        cooldown = min(120, 10 * (2 ** max(0, rt["consecutive_failures"] - 1)))
        rt["cooldown_until"] = time.time() + cooldown
        last_error = err or f"{name} failed with exit code {rc}"

    return -1, "", last_error or "No CLI backend succeeded", candidates[-1] if candidates else requested
