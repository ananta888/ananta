"""``:ai data ...`` subcommands: the local AI-Snake training data store.

Each action (path, show, export, export-md, import, compact, delete, reset)
is one handler in :data:`AI_DATA_ACTIONS`; :func:`handle_ai_data_command`
only looks it up. Storage and import/export stay in the training store and
import/export modules.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from client_surfaces.operator_tui.ai_snake_training_import_export import (
    export_training_bundle_to_path,
    export_training_markdown,
    import_training_bundle,
)
from client_surfaces.operator_tui.ai_snake_training_store import (
    build_training_bundle,
    compact_training_data,
    data_path_status,
    data_show_status,
    delete_events,
    delete_patterns,
    reset_training_data,
)
from client_surfaces.operator_tui.models import CommandResult, OperatorState

AiDataHandler = Callable[[list[str], OperatorState, dict[str, Any]], CommandResult]

AI_DATA_USAGE = (
    "ai data: path | show | export ... | export-md <path> | import <path> ... | compact | delete ... | reset"
)
AI_DATA_IMPORT_USAGE = (
    "ai data import <path> [--preview] [--disabled] [--conflict keep_higher_confidence|overwrite|keep_local|"
    "merge_counters|import_disabled_copy] [--ignore-checksum]"
)


def _reply(state: OperatorState, game: dict[str, Any], status_message: str, output: str, **kw: Any) -> CommandResult:
    return CommandResult(state.with_updates(header_logo_game=game, status_message=status_message), output, **kw)


def _path(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    return _reply(state, game, data_path_status(), "ai data path")


def _show(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    return _reply(state, game, data_show_status(), "ai data show")


def _option_value(tail: list[str], name: str) -> str:
    """Value following ``name`` (case-insensitive) in ``tail``; empty when missing."""
    idx = [item.lower() for item in tail].index(name)
    return tail[idx + 1] if idx + 1 < len(tail) else ""


def _export_format(tail: list[str], options: set[str]) -> str:
    if "--format" not in options:
        return "json"
    try:
        return str(_option_value(tail, "--format")).lower()
    except ValueError:
        return ""


def _export_target(tail: list[str], options: set[str]) -> str:
    positional = [token for token in tail if not token.startswith("--")]
    if positional and "--format" in options:
        # ignore the format value in the positional list
        format_value = _option_value(tail, "--format")
        positional = [token for token in positional if token != format_value]
    return positional[0] if positional else ""


def _export_to_stdout(state: OperatorState, game: dict[str, Any], include_events: bool) -> CommandResult:
    bundle = build_training_bundle(include_events=include_events)
    manifest = bundle.get("privacy_manifest") if isinstance(bundle.get("privacy_manifest"), dict) else {}
    warn = " warning=private_local_data" if int(manifest.get("private_local") or 0) > 0 else ""
    return _reply(state, game, f"ai data export stdout{warn}", json.dumps(bundle, ensure_ascii=False))


def _export(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    tail = [str(token).strip() for token in args[2:]]
    options = {token.lower() for token in tail}
    if _export_format(tail, options) != "json":
        return CommandResult(state, "ai data export supports --format json", handled=False)
    include_events = "--include-events" in options
    export_target = _export_target(tail, options)
    try:
        if "--stdout" in options or not export_target:
            return _export_to_stdout(state, game, include_events)
        target = export_training_bundle_to_path(output_path=export_target, include_events=include_events)
    except ValueError as exc:
        return _reply(state, game, f"ai data export failed: {exc}", "ai data export failed", handled=False)
    return _reply(state, game, f"ai data export file={target}", f"ai data export {target}")


def _export_markdown(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    if len(args) < 3:
        return CommandResult(state, "ai data export-md requires <path>", handled=False)
    md_path = str(args[2]).strip()
    json_ref = ""
    if "--json-ref" in [str(x).lower() for x in args[3:]]:
        json_ref = _option_value([str(x) for x in args[3:]], "--json-ref")
    target = export_training_markdown(output_path=md_path, json_ref=json_ref)
    return _reply(state, game, f"ai data export-md file={target}", f"ai data export-md {target}")


def _import_status_message(result: dict[str, Any], preview: bool) -> str:
    mode = "preview" if preview else "applied"
    checksum = result.get("checksum_state") if isinstance(result.get("checksum_state"), dict) else {}
    warning = str(checksum.get("warning") or "")
    warning_suffix = f" warning={warning}" if warning else ""
    return (
        f"ai data import {mode} profile={result.get('profile_name')} "
        f"patterns={result.get('patterns_result')} conflicts={result.get('conflicts')} "
        f"strategy={result.get('conflict_resolution')}{warning_suffix}"
    )


def _import(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    if len(args) < 3:
        return CommandResult(state, AI_DATA_IMPORT_USAGE, handled=False)
    source = str(args[2]).strip()
    flags = [str(x).strip() for x in args[3:]]
    lowered = [x.lower() for x in flags]
    preview = "--preview" in lowered
    strategy = "keep_higher_confidence"
    if "--conflict" in lowered:
        idx = lowered.index("--conflict")
        strategy = str(flags[idx + 1]).strip() if idx + 1 < len(flags) else strategy
    try:
        result = import_training_bundle(
            input_path=source,
            preview=preview,
            disabled="--disabled" in lowered,
            conflict_strategy=strategy,
            ignore_checksum="--ignore-checksum" in lowered or "--unsafe" in lowered,
        )
    except ValueError as exc:
        return _reply(state, game, f"ai data import failed: {exc}", "ai data import failed", handled=False)
    if str(result.get("status") or "") == "degraded":
        status = (
            f"ai data import degraded readonly reason={result.get('reason')} "
            f"schema={result.get('schema_version')}"
        )
        return _reply(state, game, status, "ai data import degraded", handled=False)
    return _reply(state, game, _import_status_message(result, preview), json.dumps(result, ensure_ascii=False))


def _compact(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    result = compact_training_data()
    status = (
        "ai data compact "
        f"patterns={result['patterns_total']} "
        f"events={result['event_before_bytes']}->{result['event_after_bytes']}"
    )
    return _reply(state, game, status, "ai data compact")


_DELETE_TARGETS: dict[str, Callable[..., Any]] = {"events": delete_events, "patterns": delete_patterns}


def _delete(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    target = str(args[2]).lower() if len(args) >= 3 else ""
    delete = _DELETE_TARGETS.get(target)
    if delete is None:
        return CommandResult(state, "ai data delete: events | patterns", handled=False)
    delete(backup=True)
    return _reply(state, game, f"ai data delete {target}", f"ai data delete {target}")


def _reset(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    reset_training_data(backup=True)
    return _reply(state, game, "ai data reset", "ai data reset")


AI_DATA_ACTIONS: dict[str, AiDataHandler] = {
    "path": _path,
    "show": _show,
    "export": _export,
    "export-md": _export_markdown,
    "import": _import,
    "compact": _compact,
    "delete": _delete,
    "reset": _reset,
}


def handle_ai_data_command(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    action = str(args[1]).lower() if len(args) > 1 else "path"
    handler = AI_DATA_ACTIONS.get(action)
    if handler is None:
        return CommandResult(state, AI_DATA_USAGE, handled=False)
    return handler(args, state, game)
