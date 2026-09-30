"""``:sources ...`` commands: source registry, packs, snapshots, citations and cache.

:func:`handle_sources_command` wires the source services once per call
(:class:`SourcesCommandContext`) and dispatches the action through
:data:`SOURCES_ACTIONS`; ``sources pack ...`` has its own table. Each handler
is one small function (SRP); a new action is one table entry (OCP).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.citation_formatter import format_citation
from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore
from client_surfaces.operator_tui.models import CommandResult, OperatorState

SOURCES_USAGE = (
    "sources: list | packs | pack show <id> | pack bootstrap <id> [--dry-run] | pack query <id> <question> | "
    "refresh <id> | snapshots <id> | cite <id> | cache <id> [clear] | import-open-notebook <path> | "
    "chat <id> <question>"
)


@dataclass(frozen=True)
class SourcesCommandContext:
    """One ``:sources`` invocation and the source services it may use."""

    args: list[str]
    state: OperatorState
    registry: Any
    snapshots: Any
    pack_service: Any
    refresh_service: Any
    cache: Any

    def usage(self, message: str) -> CommandResult:
        return CommandResult(self.state, message, handled=False)

    def reply(self, status_message: str, output: str, *, clip: bool = True) -> CommandResult:
        status = status_message[:240] if clip else status_message
        return CommandResult(self.state.with_updates(status_message=status), output)


def _has_dry_run(tokens: list[str]) -> bool:
    return any(str(x).lower() == "--dry-run" for x in tokens)


# --- packs --------------------------------------------------------------------------


def _list_packs(ctx: SourcesCommandContext) -> CommandResult:
    packs = ctx.pack_service.list_packs()
    if not packs:
        return CommandResult(ctx.state.with_updates(status_message="sources packs: none"), "[]")
    preview = " | ".join(
        f"{str(item.get('source_pack_id') or '')}:{str(item.get('display_name') or '')}" for item in packs[:10]
    )
    return ctx.reply(
        f"sources packs {len(packs)}",
        json.dumps({"count": len(packs), "packs": packs, "preview": preview}, ensure_ascii=False),
        clip=False,
    )


def _show_pack(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 3:
        return ctx.usage("sources pack show <source-pack-id>")
    source_pack_id = str(ctx.args[2]).strip()
    try:
        pack = ctx.pack_service.get_pack(source_pack_id)
    except ValueError:
        return ctx.usage(f"sources: unknown source-pack {source_pack_id}")
    selected = [
        dict(item)
        for item in list(pack.get("sources") or [])
        if isinstance(item, dict) and str(item.get("source_id") or "").strip()
    ]
    preview = " | ".join(
        f"{str(item.get('source_id') or '')}:{str(item.get('source_priority') or '-')}" for item in selected[:10]
    )
    payload = {
        "source_pack_id": source_pack_id,
        "display_name": str(pack.get("display_name") or ""),
        "source_count": len(selected),
        "sources": selected,
        "preview": preview,
    }
    return ctx.reply(f"sources pack show {source_pack_id}", json.dumps(payload, ensure_ascii=False), clip=False)


def _bootstrap_pack(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 3:
        return ctx.usage("sources pack bootstrap <source-pack-id> [--dry-run]")
    source_pack_id = str(ctx.args[2]).strip()
    result = ctx.pack_service.bootstrap(source_pack_id=source_pack_id, dry_run=_has_dry_run(ctx.args[3:]))
    msg = f"sources pack bootstrap {source_pack_id}: {str(result.get('status') or 'unknown')}"
    return ctx.reply(msg, json.dumps(result, ensure_ascii=False))


def _query_pack(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 4:
        return ctx.usage("sources pack query <source-pack-id> <question>")
    source_pack_id = str(ctx.args[2]).strip()
    query = " ".join(ctx.args[3:]).strip()
    result = ctx.pack_service.answer_preview(source_pack_id=source_pack_id, query=query)
    origins = ", ".join(list(result.get("origins") or []))
    msg = f"sources pack query {source_pack_id}: origins={origins or '-'}"
    return ctx.reply(msg, json.dumps(result, ensure_ascii=False))


_PACK_ACTIONS: dict[str, Callable[[SourcesCommandContext], CommandResult]] = {
    "show": _show_pack,
    "bootstrap": _bootstrap_pack,
    "query": _query_pack,
}


def _pack(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources pack show|bootstrap <source-pack-id> [--dry-run]")
    handler = _PACK_ACTIONS.get(str(ctx.args[1]).lower())
    if handler is None:
        return ctx.usage("sources pack show|bootstrap|query <source-pack-id> [--dry-run|question]")
    return handler(ctx)


# --- sources ------------------------------------------------------------------------


def _list_sources(ctx: SourcesCommandContext) -> CommandResult:
    parts: list[str] = []
    for item in ctx.registry.list_sources(include_disabled=True):
        source_id = str(item.get("source_id") or "")
        latest = ctx.snapshots.latest_indexed_snapshot(source_id=source_id) or {}
        parts.append(f"{source_id}:{str(latest.get('status') or 'none')}")
    msg = "sources: " + (" ".join(parts) if parts else "none")
    return ctx.reply(msg, msg)


def _refresh(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources refresh <source-id> [--dry-run]")
    source_id = str(ctx.args[1]).strip()
    report = ctx.refresh_service.refresh_source(source_id=source_id, dry_run=_has_dry_run(ctx.args[2:]))
    msg = f"sources refresh {source_id}: {str(report.get('status') or 'unknown')}"
    reason = str(report.get("reason_code") or "")
    human = str(report.get("human_message") or "")
    if reason:
        msg += f" reason={reason}"
    if human:
        msg += f" msg={human}"
    return ctx.reply(msg, json.dumps(report, ensure_ascii=False))


def _snapshots(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources snapshots <source-id>")
    source_id = str(ctx.args[1]).strip()
    rows = ctx.snapshots.list_snapshots(source_id=source_id)
    if not rows:
        return CommandResult(ctx.state.with_updates(status_message=f"sources snapshots {source_id}: empty"), "[]")
    preview = " | ".join(f"{str(item.get('snapshot_id') or '')}:{str(item.get('status') or '')}" for item in rows[:5])
    return ctx.reply(f"sources snapshots {source_id}: {preview}", json.dumps(rows, ensure_ascii=False))


def _cite(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources cite <source-id>")
    source_id = str(ctx.args[1]).strip()
    source = ctx.registry.get_source(source_id)
    if source is None:
        return ctx.usage(f"sources: unknown source_id {source_id}")
    latest = ctx.snapshots.latest_indexed_snapshot(source_id=source_id)
    citation = format_citation(descriptor=source, snapshot=latest, output_format="long")
    return ctx.reply(f"sources cite {source_id}", str(citation.get("rendered") or citation.get("long") or ""))


def _import_open_notebook(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources import-open-notebook <path>")
    from pathlib import Path

    from agent.sources.open_notebook_importer import get_open_notebook_importer

    path = Path(str(ctx.args[1]).strip()).expanduser()
    if not path.exists() or not path.is_file():
        return ctx.usage(f"sources: export file not found {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return ctx.usage(f"sources: invalid export file {path}: {exc}")
    result = get_open_notebook_importer().import_export(payload, created_by="operator_tui")
    imported = dict(result.get("imported") or {})
    msg = (
        f"sources import-open-notebook: {str(result.get('status') or 'unknown')} "
        f"sources={imported.get('sources', 0)} notes={imported.get('notes', 0)} "
        f"insights={imported.get('insights', 0)} registry={result.get('registry_source_id') or '-'}"
    )
    return ctx.reply(msg, json.dumps(result, ensure_ascii=False))


def _chat(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 3:
        return ctx.usage("sources chat <source-id> <question>")
    from agent.services.source_chat_service import get_source_chat_service

    source_id = str(ctx.args[1]).strip()
    question = " ".join(ctx.args[2:]).strip()
    try:
        payload = get_source_chat_service().answer(prompt=question, source_ref=source_id)
    except ValueError as exc:
        return ctx.usage(f"sources chat {source_id}: {exc}")
    references = list(payload.get("source_references") or [])
    labels = " | ".join(
        str(dict(item.get("extensions") or {}).get("citation_label") or item.get("title") or "")
        for item in references[:3]
    )
    msg = f"sources chat {source_id}: refs={len(references)} {labels}"
    return ctx.reply(msg, json.dumps(payload, ensure_ascii=False))


def _cache(ctx: SourcesCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.usage("sources cache <source-id> [clear]")
    source_id = str(ctx.args[1]).strip()
    if ctx.registry.get_source(source_id) is None:
        return ctx.usage(f"sources: unknown source_id {source_id}")
    op = str(ctx.args[2]).lower() if len(ctx.args) > 2 else "status"
    if op == "clear":
        removed = int(ctx.cache.clear_source(source_id=source_id))
        stats = ctx.cache.stats_for_source(source_id=source_id)
        msg = (
            f"sources cache {source_id} cleared removed={removed} "
            f"raw={stats['raw_files']} extracted={stats['extracted_files']} bytes={stats['total_bytes']}"
        )
        return ctx.reply(msg, msg)
    stats = ctx.cache.stats_for_source(source_id=source_id)
    msg = (
        f"sources cache {source_id} raw={stats['raw_files']} extracted={stats['extracted_files']} "
        f"bytes={stats['total_bytes']}"
    )
    return ctx.reply(msg, msg)


SOURCES_ACTIONS: dict[str, Callable[[SourcesCommandContext], CommandResult]] = {
    "packs": _list_packs,
    "pack": _pack,
    "list": _list_sources,
    "refresh": _refresh,
    "snapshots": _snapshots,
    "cite": _cite,
    "import-open-notebook": _import_open_notebook,
    "chat": _chat,
    "cache": _cache,
}


def _sources_context(args: list[str], state: OperatorState) -> SourcesCommandContext:
    """Wire the source services and register missing built-in sources."""
    registry = SourceRegistry()
    snapshots = SourceSnapshotStore()
    pack_service = SourcePackService(registry=registry, snapshots=snapshots)
    for descriptor in load_builtin_source_descriptors():
        source_id = str(descriptor.get("source_id") or "").strip()
        if source_id and registry.get_source(source_id) is None:
            registry.create_source(descriptor)
    refresh_service = SourceRefreshService(registry=registry, snapshots=snapshots)
    return SourcesCommandContext(
        args=args,
        state=state,
        registry=registry,
        snapshots=snapshots,
        pack_service=pack_service,
        refresh_service=refresh_service,
        cache=refresh_service.cache,
    )


def handle_sources_command(args: list[str], state: OperatorState) -> CommandResult:
    action = str(args[0]).lower() if args else "list"
    ctx = _sources_context(args, state)
    handler = SOURCES_ACTIONS.get(action)
    if handler is None:
        return CommandResult(state, SOURCES_USAGE, handled=False)
    return handler(ctx)
