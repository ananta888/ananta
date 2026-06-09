"""Workspace-runtime command rewriting for the task-scoped service.

Extracted from ``agent.services.task_scoped_execution_service`` as
SPLIT-001v. The module owns one concern: deciding whether a runtime
command needs to be rewritten so it picks up the workspace's local
virtual environment instead of the global interpreter.

Two rewrite strategies are tried in order:

1. ``workspace_venv_uvicorn_binary`` — when the command mentions a
   bare ``uvicorn`` token (not already prefixed by a path) **and** the
   workspace ships its own ``.venv/bin/uvicorn`` binary, the binary
   reference is substituted in place. This keeps shell operators,
   environment-variable assignments, and arguments untouched.
2. ``workspace_venv_activate_prefix`` — when the workspace ships a
   ``.venv/bin/activate`` script and the command does not already
   source it, the activate script is prepended via
   ``source .venv/bin/activate && <command>``.

The return value is ``(rewritten_command, meta)``. ``meta`` is a small
dict that explains which strategy was applied (or ``None`` if the
command was returned unchanged). Downstream telemetry uses ``meta`` to
report the rewrite, but never relies on it for correctness.

Backwards compatibility is preserved at the service boundary via a
thin delegating wrapper in :class:`TaskScopedExecutionService`
(12-month deprecation window, see
``todos/todo.refactor-large-files-split.json`` SPLIT-001).
"""

from __future__ import annotations

import re
from pathlib import Path


_UVICORN_TOKEN_PATTERN = re.compile(r"(?<![\\w./-])uvicorn(?![\\w./-])")


def rewrite_runtime_command_for_workspace_tools(
    *,
    command: str | None,
    workspace_dir: str | None,
) -> tuple[str | None, dict | None]:
    """Rewrite ``command`` to use the workspace's venv when applicable.

    Returns ``(command, None)`` if no rewrite is needed (empty command,
    no workspace dir, command does not mention ``uvicorn``, or the
    workspace has neither ``.venv/bin/uvicorn`` nor ``.venv/bin/activate``).

    Otherwise returns ``(rewritten, meta)`` with ``meta["strategy"]``
    set to either ``"workspace_venv_uvicorn_binary"`` or
    ``"workspace_venv_activate_prefix"``.
    """
    command_text = str(command or "").strip()
    workspace = str(workspace_dir or "").strip()
    if not command_text or not workspace:
        return command, None
    if "uvicorn" not in command_text:
        return command, None

    venv_uvicorn = Path(workspace) / ".venv" / "bin" / "uvicorn"
    if venv_uvicorn.exists():
        # Replace bare uvicorn token only, keep shell operators/arguments unchanged.
        rewritten = _UVICORN_TOKEN_PATTERN.sub(str(venv_uvicorn), command_text)
        if rewritten != command_text:
            return rewritten, {
                "strategy": "workspace_venv_uvicorn_binary",
                "from": "uvicorn",
                "to": str(venv_uvicorn),
            }

    venv_activate = Path(workspace) / ".venv" / "bin" / "activate"
    if venv_activate.exists() and ".venv/bin/activate" not in command_text:
        rewritten = f"source .venv/bin/activate && {command_text}"
        return rewritten, {
            "strategy": "workspace_venv_activate_prefix",
            "activate_script": ".venv/bin/activate",
        }
    return command, None


_rewrite_runtime_command_for_workspace_tools = rewrite_runtime_command_for_workspace_tools
