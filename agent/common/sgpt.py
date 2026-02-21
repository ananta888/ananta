import logging
import os
import shutil
import subprocess
import sys
import threading
import time

from agent.config import settings

sgpt_lock = threading.Lock()

SUPPORTED_CLI_BACKENDS = {"sgpt", "opencode", "aider", "mistral_code"}
CLI_BACKEND_CAPABILITIES = {
    "sgpt": {
        "display_name": "ShellGPT",
        "supports_model": True,
        "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"],
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
        data[name] = {
            "binary_path": _resolve_backend_binary(name),
            "binary_available": bool(_resolve_backend_binary(name)),
            "health_score": _health_score(name),
            "cooldown_active": cooldown_until > now,
            "cooldown_until": cooldown_until,
            **rt,
        }
    return data


def get_cli_backend_capabilities() -> dict[str, dict]:
    return {k: dict(v) for k, v in CLI_BACKEND_CAPABILITIES.items()}


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
        code_pref = ["aider", "opencode", "mistral_code", "sgpt"]
        ordered = [c for c in code_pref if c in candidates]
        for c in candidates:
            if c not in ordered:
                ordered.append(c)
        candidates = ordered

    now = time.time()
    active = []
    cooled = []
    for c in candidates:
        until = float(_BACKEND_RUNTIME.get(c, {}).get("cooldown_until") or 0.0)
        if until > now and len(candidates) > 1:
            cooled.append(c)
            continue
        active.append(c)
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
