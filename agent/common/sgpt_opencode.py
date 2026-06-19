from __future__ import annotations

import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile

from flask import current_app, has_app_context

from agent.config import settings
from agent.llm_integration import (
    _find_matching_lmstudio_candidate,
    _find_matching_ollama_candidate,
    probe_lmstudio_runtime,
    probe_ollama_runtime,
    resolve_ollama_model,
)
from agent.local_llm_backends import resolve_local_openai_backend
from agent.model_selection import normalize_legacy_model_name
from agent.common.sgpt_helpers import (
    _classify_runtime_target,
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_ollama_openai_base_url,
    _normalize_openai_base_url,
    _resolve_openai_compatible_base_url,
    _resolve_profile_api_key,
)
from agent.common.sgpt_backend_semaphore import _acquire_backend_permit

log = logging.getLogger(__name__)


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
    target_profile = str(opencode_runtime_cfg.get("target_profile") or "").strip() or None
    forced_target_provider = str(opencode_runtime_cfg.get("target_provider") or "").strip().lower() or None
    forced_target_model = str(opencode_runtime_cfg.get("target_model") or opencode_runtime_cfg.get("model") or "").strip() or None
    resolved_profile = None
    if target_profile:
        try:
            from agent.services.model_invocation_service import ModelInvocationService

            resolver = ModelInvocationService._get_resolver()
            resolved_profile = (getattr(resolver, "_by_id", {}) or {}).get(target_profile) if resolver is not None else None
            if resolved_profile is not None:
                forced_target_provider = str(getattr(resolved_profile, "provider_id", "") or "").strip().lower() or forced_target_provider
                forced_target_model = str(getattr(resolved_profile, "model", "") or "").strip() or forced_target_model
        except Exception:
            resolved_profile = None
    _native_passthrough = {"opencode", "anthropic", "openai", "gemini", "groq", "openrouter", "bedrock", "azure", "vertexai", "copilot", "native"}
    if forced_target_provider in _native_passthrough:
        forced_target_provider = "__native__"
    elif forced_target_provider not in {"ollama", "lmstudio"}:
        forced_target_provider = None
    configured_default_model = (
        forced_target_model
        or str(agent_cfg.get("opencode_default_model") or "").strip()
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

    if target_provider == "__native__":
        return {
            "model": None,
            "target_provider": None,
            "target_model": None,
            "base_url": None,
            "base_url_source": None,
            "target_kind": "native_opencode",
            "target_provider_type": None,
            "tool_mode": tool_mode,
            "execution_mode": execution_mode,
            "provider_config": None,
            "diagnostics": [],
        }
    elif target_provider == "ollama":
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
    elif target_provider and target_model and target_provider in built_in_providers:
        cli_model = f"{target_provider}/{target_model}"

    diagnostics = _build_opencode_runtime_diagnostics(base_url=base_url) if (target_provider in {"ollama", "lmstudio"} or local_target) else []
    return {
        "model": cli_model,
        "target_profile": target_profile,
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


def run_opencode_command(
    prompt: str,
    model: str | None = None,
    timeout: int = 60,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """Führt einen OpenCode-CLI-Aufruf aus. Gibt (returncode, stdout, stderr) zurück."""
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

    with _acquire_backend_permit("opencode", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'opencode' ist ausgelastet (semaphore_exhausted)", opencode_bin
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
                log.warning("OpenCode runtime diagnostics: %s", ",".join(diagnostics))
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
                log.info(f"Zentraler OpenCode-Aufruf: {args}")
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
            log.error("OpenCode Timeout")
            return -1, "", "Timeout", " ".join(shlex.quote(part) for part in args)
        except Exception as e:
            log.exception(f"OpenCode Fehler: {e}")
            return -1, "", str(e), " ".join(shlex.quote(part) for part in args)


def resolve_codex_runtime_config() -> dict:
    agent_cfg = _get_agent_config()
    provider_urls = _get_runtime_provider_urls()
    if has_app_context() and "PROVIDER_URLS" in current_app.config:
        explicit_provider_urls = current_app.config.get("PROVIDER_URLS") or {}
        if isinstance(explicit_provider_urls, dict) and not explicit_provider_urls:
            provider_urls = {}
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
    elif provider_urls == {}:
        base_url = None
        base_url_source = None
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
    is_local = _is_probably_local_base_url(base_url)
    if not api_key and is_local:
        api_key = "sk-no-key-needed"
        api_key_source = "local_dummy"
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key
        if api_key:
            api_key_source = "openai_api_key"
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
    """Fuehrt einen OpenAI Codex CLI exec-Aufruf aus."""
    codex_bin = settings.codex_path or "codex"
    codex_resolved = shutil.which(codex_bin)
    if codex_resolved is None:
        return -1, "", (f"Codex binary '{codex_bin}' not found. Install with: npm i -g @openai/codex")

    args = [codex_resolved, "exec", "--skip-git-repo-check"]
    selected_model = model or settings.codex_default_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append(prompt)

    with _acquire_backend_permit("codex", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'codex' ist ausgelastet (semaphore_exhausted)"
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
            log.warning("Codex runtime diagnostics: %s", ",".join(diagnostics))

        try:
            log.info(f"Zentraler Codex-Aufruf: {args}")
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
            log.error("Codex Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            log.exception(f"Codex Fehler: {e}")
            return -1, "", str(e)


def run_aider_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """Führt einen Aider-CLI-Aufruf aus (non-interactive)."""
    aider_bin = settings.aider_path or "aider"
    aider_resolved = shutil.which(aider_bin)
    if aider_resolved is None:
        return -1, "", (f"Aider binary '{aider_bin}' not found. Install with: pip install aider-chat")

    args = [aider_resolved, "--message", prompt, "--yes-always"]
    selected_model = model or settings.aider_default_model
    if selected_model:
        args.extend(["--model", selected_model])

    with _acquire_backend_permit("aider", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'aider' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()
        try:
            log.info(f"Zentraler Aider-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            log.error("Aider Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            log.exception(f"Aider Fehler: {e}")
            return -1, "", str(e)


def run_mistral_code_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """Führt einen Mistral-Code-CLI-Aufruf aus."""
    mistral_bin = settings.mistral_code_path or "mistral-code"
    mistral_resolved = shutil.which(mistral_bin)
    if mistral_resolved is None:
        return -1, "", (f"Mistral Code binary '{mistral_bin}' not found. Install with: npm i -g mistral-code")

    args = [mistral_resolved]

    with _acquire_backend_permit("mistral_code", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'mistral_code' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()
        if not env.get("MISTRAL_API_KEY") and getattr(settings, "mistral_api_key", None):
            env["MISTRAL_API_KEY"] = settings.mistral_api_key
        try:
            log.info(f"Zentraler Mistral-Code-Aufruf: {args}")
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
            log.error("Mistral Code Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            log.exception(f"Mistral Code Fehler: {e}")
            return -1, "", str(e)
