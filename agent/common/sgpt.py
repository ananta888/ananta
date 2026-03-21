import logging
import os
import shutil
import subprocess
import sys
import threading
import time

from flask import current_app, has_app_context

from agent.config import settings

sgpt_lock = threading.Lock()

SUPPORTED_CLI_BACKENDS = {"sgpt", "codex", "opencode", "aider", "mistral_code"}
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
            runtime_entry["target_base_url_source"] = codex_runtime["base_url_source"]
            runtime_entry["target_is_local"] = codex_runtime["is_local"]
            runtime_entry["api_key_configured"] = bool(codex_runtime["api_key"])
            runtime_entry["api_key_source"] = codex_runtime["api_key_source"]
            runtime_entry["prefer_lmstudio"] = codex_runtime["prefer_lmstudio"]
        data[name] = runtime_entry
    return data


def get_cli_backend_capabilities() -> dict[str, dict]:
    return {k: dict(v) for k, v in CLI_BACKEND_CAPABILITIES.items()}


def _prioritize_code_backends(candidates: list[str]) -> list[str]:
    code_pref = ["codex", "aider", "opencode", "mistral_code", "sgpt"]
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


def run_opencode_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Führt einen OpenCode-CLI-Aufruf aus.
    Gibt (returncode, stdout, stderr) zurück.
    """
    opencode_bin = settings.opencode_path or "opencode"
    opencode_resolved = shutil.which(opencode_bin)
    if opencode_resolved is None:
        return -1, "", (f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai")

    args = [opencode_resolved, "run"]
    selected_model = model or settings.opencode_default_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append(prompt)

    with sgpt_lock:
        env = os.environ.copy()
        try:
            logging.info(f"Zentraler OpenCode-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("OpenCode Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"OpenCode Fehler: {e}")
            return -1, "", str(e)


def _resolve_openai_compatible_base_url() -> str | None:
    provider = (settings.default_provider or "").strip().lower()
    if provider == "lmstudio":
        raw_url = settings.lmstudio_url
    elif provider in {"openai", "codex"}:
        raw_url = settings.openai_url
    else:
        raw_url = settings.openai_url or settings.lmstudio_url

    if not raw_url:
        return None

    normalized = raw_url.strip()
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _normalize_openai_base_url(url: str | None) -> str | None:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None
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


def resolve_codex_runtime_config() -> dict[str, str | bool | None]:
    agent_cfg = _get_agent_config()
    codex_cfg = agent_cfg.get("codex_cli") or {}
    if not isinstance(codex_cfg, dict):
        codex_cfg = {}

    explicit_base_url = _normalize_openai_base_url(codex_cfg.get("base_url"))
    prefer_lmstudio = codex_cfg.get("prefer_lmstudio")
    if prefer_lmstudio is None:
        prefer_lmstudio = (settings.default_provider or "").strip().lower() == "lmstudio"

    if explicit_base_url:
        base_url = explicit_base_url
        base_url_source = "codex_cli.base_url"
    elif prefer_lmstudio:
        base_url = _normalize_openai_base_url(settings.lmstudio_url)
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
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key
        if api_key:
            api_key_source = "openai_api_key"
    if not api_key and _is_probably_local_base_url(base_url):
        api_key = "sk-no-key-needed"
        api_key_source = "local_dummy"
    return {
        "base_url": base_url,
        "api_key": api_key,
        "base_url_source": base_url_source if base_url else None,
        "api_key_source": api_key_source,
        "is_local": _is_probably_local_base_url(base_url),
        "prefer_lmstudio": bool(prefer_lmstudio),
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
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            env["OPENAI_API_BASE"] = base_url

        if api_key:
            env["OPENAI_API_KEY"] = api_key

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
    routing_policy: dict | None = None,
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
            rc, out, err = run_opencode_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "aider":
            rc, out, err = run_aider_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "mistral_code":
            rc, out, err = run_mistral_code_command(prompt=prompt, model=model, timeout=timeout)
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
