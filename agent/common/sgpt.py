import subprocess
import os
import sys
import logging
import threading
import shutil
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


def get_cli_backend_capabilities() -> dict[str, dict]:
    return {k: dict(v) for k, v in CLI_BACKEND_CAPABILITIES.items()}


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
            result = subprocess.run(
                [sys.executable, "-m", "sgpt"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout
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
        return -1, "", (
            f"OpenCode binary '{opencode_bin}' not found. "
            "Install with: npm i -g opencode-ai"
        )

    args = [opencode_resolved, "run"]
    selected_model = model or settings.opencode_default_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append(prompt)

    with sgpt_lock:
        env = os.environ.copy()
        try:
            logging.info(f"Zentraler OpenCode-Aufruf: {args}")
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout
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
        return -1, "", (
            f"Aider binary '{aider_bin}' not found. "
            "Install with: pip install aider-chat"
        )

    args = [aider_resolved, "--message", prompt, "--yes-always"]
    selected_model = model or settings.aider_default_model
    if selected_model:
        args.extend(["--model", selected_model])

    with sgpt_lock:
        env = os.environ.copy()
        try:
            logging.info(f"Zentraler Aider-Aufruf: {args}")
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout
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
        return -1, "", (
            f"Mistral Code binary '{mistral_bin}' not found. "
            "Install with: npm i -g mistral-code"
        )

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
            result = subprocess.run(
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
) -> tuple[int, str, str, str]:
    """
    Führt den konfigurierten CLI-Backend-Aufruf aus.
    Rückgabe: (returncode, stdout, stderr, backend_used)
    """
    requested = (backend or "sgpt").strip().lower()
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

    last_error = ""
    for name in candidates:
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

        if rc == 0 or out:
            return rc, out, err, name
        last_error = err or f"{name} failed with exit code {rc}"

    return -1, "", last_error or "No CLI backend succeeded", candidates[-1] if candidates else requested
