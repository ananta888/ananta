"""Claude Code runtime configuration and non-interactive execution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any


def resolve_claude_runtime_config(
    *,
    agent_config: Mapping[str, Any],
    settings: Any,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    claude_cfg = agent_config.get("claude_cli") or {}
    if not isinstance(claude_cfg, dict):
        claude_cfg = {}

    enabled = bool(claude_cfg.get("enabled", False))
    command = str(
        claude_cfg.get("command")
        or getattr(settings, "claude_path", "claude")
        or "claude"
    ).strip() or "claude"
    raw_auth_mode = claude_cfg.get("auth_mode") if isinstance(claude_cfg.get("auth_mode"), str) else None
    if raw_auth_mode is not None:
        auth_mode = raw_auth_mode.strip().lower() or "claude_login"
    else:
        auth_mode = str(
            getattr(settings, "claude_auth_mode", "claude_login") or "claude_login"
        ).strip().lower() or "claude_login"
    if auth_mode not in ("claude_login", "api_key"):
        auth_mode = "claude_login"

    default_model = str(
        claude_cfg.get("default_model")
        or getattr(settings, "claude_default_model", "")
        or ""
    ).strip() or None
    permission_mode = str(
        claude_cfg.get("permission_mode")
        or getattr(settings, "claude_permission_mode", "plan")
        or "plan"
    ).strip()
    if permission_mode == "default":
        permission_mode = "manual"
    if permission_mode not in {"plan", "manual", "acceptEdits", "dontAsk", "auto"}:
        permission_mode = "plan"

    def bounded(value: object, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    timeout_seconds = bounded(
        claude_cfg.get("timeout_seconds", getattr(settings, "claude_timeout_seconds", 1800)),
        default=1800,
        minimum=30,
        maximum=14400,
    )
    max_concurrent_runs = bounded(
        claude_cfg.get("max_concurrent_runs", getattr(settings, "claude_max_concurrent_runs", 1)),
        default=1,
        minimum=1,
        maximum=8,
    )
    allowed_paths = [str(path) for path in (claude_cfg.get("allowed_paths") or []) if str(path or "").strip()]
    diagnostics: list[str] = []
    if not enabled:
        diagnostics.append("claude_cli_disabled")
    if auth_mode == "api_key" and not (
        environ.get("ANTHROPIC_API_KEY") or getattr(settings, "anthropic_api_key", None)
    ):
        diagnostics.append("claude_runtime_missing_api_key")
    return {
        "enabled": enabled,
        "command": command,
        "auth_mode": auth_mode,
        "api_key_required": auth_mode == "api_key",
        "default_model": default_model,
        "permission_mode": permission_mode,
        "timeout_seconds": timeout_seconds,
        "max_concurrent_runs": max_concurrent_runs,
        "allowed_paths": allowed_paths,
        "write_armed_default": bool(claude_cfg.get("write_armed_default", False)),
        "diagnostics": diagnostics,
    }


def run_claude_command(
    prompt: str,
    *,
    model: str | None,
    timeout: int | None,
    workdir: str | None,
    runtime_config: Mapping[str, Any],
    settings: Any,
    which: Callable[[str], str | None],
    provisioned_binary: Callable[[str], str | None],
    acquire_permit: Callable[..., AbstractContextManager[Any]],
    run_process: Callable[..., Any],
    logger: Any,
    environ: Mapping[str, str],
) -> tuple[int, str, str]:
    if not runtime_config["enabled"]:
        return -1, "", (
            "Claude CLI backend ist deaktiviert (claude_cli.enabled=false). "
            "Aktivieren via POST /config mit {'claude_cli': {'enabled': true}}."
        )
    claude_bin = str(runtime_config["command"])
    claude_resolved = which(claude_bin) or provisioned_binary("claude_code")
    if claude_resolved is None:
        return -1, "", (
            f"Claude binary '{claude_bin}' not found. Install with: npm i -g @anthropic-ai/claude-code"
        )
    allowed_paths = list(runtime_config["allowed_paths"])
    if workdir and allowed_paths:
        workdir_abs = os.path.realpath(workdir)
        if not any(
            workdir_abs == os.path.realpath(path)
            or workdir_abs.startswith(os.path.realpath(path) + os.sep)
            for path in allowed_paths
        ):
            return -1, "", f"Workdir '{workdir}' liegt ausserhalb von claude_cli.allowed_paths"

    effective_timeout = int(timeout or runtime_config["timeout_seconds"])
    args = [
        claude_resolved,
        "-p",
        prompt,
        "--permission-mode",
        str(runtime_config["permission_mode"]),
        "--output-format",
        "text",
    ]
    selected_model = str(model or runtime_config["default_model"] or "").strip()
    if selected_model and selected_model not in ("claude-code-default", "default"):
        args.extend(["--model", selected_model])

    with acquire_permit("claude_code", timeout=effective_timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'claude_code' ist ausgelastet (semaphore_exhausted)"
        environment = dict(environ)
        if runtime_config["auth_mode"] == "claude_login":
            environment.pop("ANTHROPIC_API_KEY", None)
        elif not environment.get("ANTHROPIC_API_KEY") and getattr(settings, "anthropic_api_key", None):
            environment["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        diagnostics = list(runtime_config.get("diagnostics") or [])
        if diagnostics:
            logger.warning("Claude runtime diagnostics: %s", ",".join(diagnostics))
        try:
            logger.info("Zentraler Claude-Code-Aufruf: %s", args[:1] + ["-p", "<prompt>"] + args[3:])
            result = run_process(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=effective_timeout,
                cwd=workdir or None,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Claude Code Timeout")
            return -1, "", "Timeout"
        except Exception as exc:
            logger.exception("Claude Code Fehler: %s", exc)
            return -1, "", str(exc)
