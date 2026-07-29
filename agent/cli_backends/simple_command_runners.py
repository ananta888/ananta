"""Process runners for the small Aider and Mistral Code CLI adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from typing import Any


def run_aider_command(
    prompt: str,
    model: str | None,
    timeout: int,
    *,
    settings: Any,
    which: Callable[[str], str | None],
    run_process: Callable[..., Any],
    acquire_permit: Callable[..., Any],
    logger: Any,
    environ: Mapping[str, str],
) -> tuple[int, str, str]:
    """Run one non-interactive Aider command."""

    aider_bin = settings.aider_path or "aider"
    aider_resolved = which(aider_bin)
    if aider_resolved is None:
        return -1, "", (f"Aider binary '{aider_bin}' not found. Install with: pip install aider-chat")
    args = [aider_resolved, "--message", prompt, "--yes-always"]
    selected_model = model or settings.aider_default_model
    if selected_model:
        args.extend(["--model", selected_model])

    with acquire_permit("aider", timeout=timeout) as ticket:
        if not ticket.acquired:
            return (
                -1,
                "",
                "Backend 'aider' ist ausgelastet (semaphore_exhausted)",
            )
        env = environ.copy()
        try:
            logger.info(f"Zentraler Aider-Aufruf: {args}")
            result = run_process(
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
            logger.error("Aider Timeout")
            return -1, "", "Timeout"
        except Exception as exc:
            logger.exception(f"Aider Fehler: {exc}")
            return -1, "", str(exc)


def run_mistral_code_command(
    prompt: str,
    model: str | None,
    timeout: int,
    *,
    settings: Any,
    which: Callable[[str], str | None],
    run_process: Callable[..., Any],
    acquire_permit: Callable[..., Any],
    logger: Any,
    environ: Mapping[str, str],
) -> tuple[int, str, str]:
    """Run one Mistral Code command through its stdin protocol."""

    mistral_bin = settings.mistral_code_path or "mistral-code"
    mistral_resolved = which(mistral_bin)
    if mistral_resolved is None:
        return -1, "", (f"Mistral Code binary '{mistral_bin}' not found. Install with: npm i -g mistral-code")
    args = [mistral_resolved]

    with acquire_permit("mistral_code", timeout=timeout) as ticket:
        if not ticket.acquired:
            return (
                -1,
                "",
                "Backend 'mistral_code' ist ausgelastet (semaphore_exhausted)",
            )
        env = dict(environ)
        if not env.get("MISTRAL_API_KEY") and getattr(settings, "mistral_api_key", None):
            env["MISTRAL_API_KEY"] = settings.mistral_api_key
        try:
            logger.info(f"Zentraler Mistral-Code-Aufruf: {args}")
            input_lines = [prompt]
            selected_model = model or settings.mistral_code_default_model
            if selected_model:
                input_lines.append(f"/model {selected_model}")
            input_lines.append("exit")
            result = run_process(
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
            logger.error("Mistral Code Timeout")
            return -1, "", "Timeout"
        except Exception as exc:
            logger.exception(f"Mistral Code Fehler: {exc}")
            return -1, "", str(exc)
