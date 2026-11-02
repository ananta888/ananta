"""CLI invocation helper for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as part of
SPLIT-001 (sub-split 001r). The module owns the small, well-isolated
utility that adapts a caller-provided ``cli_runner`` to a given kwargs
dict by introspecting the runner's signature and dropping unknown keys.

The runner is the seam between the task-scoped service and the LLM
CLI backends (sgpt / codex / native worker). Different backends expose
slightly different keyword surfaces; this helper lets the service
forward the same kwargs dict to any of them without crashing on
unexpected keys.

Backwards compatibility is preserved at the service boundary via a thin
delegating wrapper in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json
SPLIT-001).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def invoke_cli_runner(cli_runner: Callable, **cli_kwargs: Any) -> Any:
    """Invoke ``cli_runner`` with a kwargs subset that matches its signature.

    Behaviour:

    1. If ``cli_runner`` is a ``Mock``/``MagicMock`` (test double) with a
       ``side_effect`` callable, the signature is taken from the side
       effect (otherwise unittest's auto-spec wraps the signature in a way
       that would reject all real kwargs).
    2. The runner is invoked as ``cli_runner(**cli_kwargs)`` when:
       * the signature cannot be introspected (``TypeError`` /
         ``ValueError``), or
       * the signature declares ``**kwargs`` (i.e. any keyword is
         accepted).
    3. Otherwise the kwargs are filtered to the keys the runner actually
       declares, and the runner is invoked with that filtered dict.

    Returns the runner's return value.
    """
    signature_target = cli_runner
    side_effect = getattr(cli_runner, "side_effect", None)
    if callable(side_effect):
        signature_target = side_effect
    try:
        signature = inspect.signature(signature_target)
    except (TypeError, ValueError):
        return cli_runner(**cli_kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return cli_runner(**cli_kwargs)
    filtered_kwargs = {key: value for key, value in cli_kwargs.items() if key in signature.parameters}
    return cli_runner(**filtered_kwargs)


_invoke_cli_runner = invoke_cli_runner


def coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
    """Return ``(text, source)`` picking stdout if non-empty, else stderr, else ""/none.

    The ``source`` tag is downstream-consumed for telemetry (it tells
    the caller whether the captured text came from the worker stdout,
    stderr, or nothing at all). Both strings are stripped before
    testing emptiness.
    """
    out = str(stdout or "").strip()
    if out:
        return out, "stdout"
    err = str(stderr or "").strip()
    if err:
        return err, "stderr"
    return "", "none"


_coalesce_cli_output = coalesce_cli_output


def normalize_tool_calls(tool_calls: object) -> list[dict] | None:
    """Coerce a tool-calls payload into a uniform ``list[dict]`` shape.

    * A ``list`` of ``dict`` is returned unchanged.
    * A single ``dict`` is wrapped in a one-element list.
    * Anything else (``None``, scalar, mixed-type list) returns ``None``,
      which the caller is expected to treat as "no tool calls present".
    """
    if isinstance(tool_calls, list) and all(isinstance(item, dict) for item in tool_calls):
        return tool_calls
    if isinstance(tool_calls, dict):
        return [tool_calls]
    return None


_normalize_tool_calls = normalize_tool_calls
