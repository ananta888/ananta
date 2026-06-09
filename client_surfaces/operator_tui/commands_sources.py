from __future__ import annotations

import json

from agent.sources.citation_formatter import format_citation
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_snapshot_store import SourceSnapshotStore
from client_surfaces.operator_tui.models import CommandResult, OperatorState


def handle_sources_command(args: list[str], state: OperatorState) -> CommandResult:
    action = str(args[0]).lower() if args else "list"
    registry = SourceRegistry()
    snapshots = SourceSnapshotStore()
    pack_service = SourcePackService(registry=registry, snapshots=snapshots)
    cache = refresh_service = None
    for descriptor in load_builtin_source_descriptors():
        source_id = str(descriptor.get("source_id") or "").strip()
        if source_id and registry.get_source(source_id) is None:
            registry.create_source(descriptor)
    refresh_service = SourceRefreshService(registry=registry, snapshots=snapshots)
    cache = refresh_service.cache
    if action == "packs":
        packs = pack_service.list_packs()
        if not packs:
            return CommandResult(state.with_updates(status_message="sources packs: none"), "[]")
        preview = " | ".join(
            f"{str(item.get('source_pack_id') or '')}:{str(item.get('display_name') or '')}"
            for item in packs[:10]
        )
        return CommandResult(
            state.with_updates(status_message=f"sources packs {len(packs)}"),
            json.dumps({"count": len(packs), "packs": packs, "preview": preview}, ensure_ascii=False),
        )
    if action == "pack":
        if len(args) < 2:
            return CommandResult(state, "sources pack show|bootstrap <source-pack-id> [--dry-run]", handled=False)
        sub = str(args[1]).lower()
        if sub == "show":
            if len(args) < 3:
                return CommandResult(state, "sources pack show <source-pack-id>", handled=False)
            source_pack_id = str(args[2]).strip()
            try:
                pack = pack_service.get_pack(source_pack_id)
            except ValueError:
                return CommandResult(state, f"sources: unknown source-pack {source_pack_id}", handled=False)
            selected = [
                dict(item) for item in list(pack.get("sources") or [])
                if isinstance(item, dict) and str(item.get("source_id") or "").strip()
            ]
            preview = " | ".join(
                f"{str(item.get('source_id') or '')}:{str(item.get('source_priority') or '-')}"
                for item in selected[:10]
            )
            payload = {
                "source_pack_id": source_pack_id,
                "display_name": str(pack.get("display_name") or ""),
                "source_count": len(selected),
                "sources": selected,
                "preview": preview,
            }
            return CommandResult(
                state.with_updates(status_message=f"sources pack show {source_pack_id}"),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "bootstrap":
            if len(args) < 3:
                return CommandResult(state, "sources pack bootstrap <source-pack-id> [--dry-run]", handled=False)
            source_pack_id = str(args[2]).strip()
            dry_run = any(str(x).lower() == "--dry-run" for x in args[3:])
            result = pack_service.bootstrap(source_pack_id=source_pack_id, dry_run=dry_run)
            msg = f"sources pack bootstrap {source_pack_id}: {str(result.get('status') or 'unknown')}"
            return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(result, ensure_ascii=False))
        if sub == "query":
            if len(args) < 4:
                return CommandResult(state, "sources pack query <source-pack-id> <question>", handled=False)
            source_pack_id = str(args[2]).strip()
            query = " ".join(args[3:]).strip()
            result = pack_service.answer_preview(source_pack_id=source_pack_id, query=query)
            origins = ", ".join(list(result.get("origins") or []))
            msg = f"sources pack query {source_pack_id}: origins={origins or '-'}"
            return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(result, ensure_ascii=False))
        return CommandResult(state, "sources pack show|bootstrap|query <source-pack-id> [--dry-run|question]", handled=False)
    if action == "list":
        items = registry.list_sources(include_disabled=True)
        parts: list[str] = []
        for item in items:
            source_id = str(item.get("source_id") or "")
            latest = snapshots.latest_indexed_snapshot(source_id=source_id) or {}
            status = str(latest.get("status") or "none")
            parts.append(f"{source_id}:{status}")
        msg = "sources: " + (" ".join(parts) if parts else "none")
        return CommandResult(state.with_updates(status_message=msg[:240]), msg)
    if action == "refresh":
        if len(args) < 2:
            return CommandResult(state, "sources refresh <source-id> [--dry-run]", handled=False)
        source_id = str(args[1]).strip()
        dry_run = any(str(x).lower() == "--dry-run" for x in args[2:])
        report = refresh_service.refresh_source(source_id=source_id, dry_run=dry_run)
        status = str(report.get("status") or "unknown")
        reason = str(report.get("reason_code") or "")
        human = str(report.get("human_message") or "")
        msg = f"sources refresh {source_id}: {status}"
        if reason:
            msg += f" reason={reason}"
        if human:
            msg += f" msg={human}"
        return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(report, ensure_ascii=False))
    if action == "snapshots":
        if len(args) < 2:
            return CommandResult(state, "sources snapshots <source-id>", handled=False)
        source_id = str(args[1]).strip()
        rows = snapshots.list_snapshots(source_id=source_id)
        if not rows:
            return CommandResult(state.with_updates(status_message=f"sources snapshots {source_id}: empty"), "[]")
        preview = " | ".join(
            f"{str(item.get('snapshot_id') or '')}:{str(item.get('status') or '')}" for item in rows[:5]
        )
        return CommandResult(
            state.with_updates(status_message=f"sources snapshots {source_id}: {preview}"[:240]),
            json.dumps(rows, ensure_ascii=False),
        )
    if action == "cite":
        if len(args) < 2:
            return CommandResult(state, "sources cite <source-id>", handled=False)
        source_id = str(args[1]).strip()
        source = registry.get_source(source_id)
        if source is None:
            return CommandResult(state, f"sources: unknown source_id {source_id}", handled=False)
        latest = snapshots.latest_indexed_snapshot(source_id=source_id)
        citation = format_citation(descriptor=source, snapshot=latest, output_format="long")
        rendered = str(citation.get("rendered") or citation.get("long") or "")
        return CommandResult(
            state.with_updates(status_message=f"sources cite {source_id}"[:240]),
            rendered,
        )
    if action == "cache":
        if len(args) < 2:
            return CommandResult(state, "sources cache <source-id> [clear]", handled=False)
        source_id = str(args[1]).strip()
        if registry.get_source(source_id) is None:
            return CommandResult(state, f"sources: unknown source_id {source_id}", handled=False)
        op = str(args[2]).lower() if len(args) > 2 else "status"
        if op == "clear":
            removed = int(cache.clear_source(source_id=source_id))
            stats = cache.stats_for_source(source_id=source_id)
            msg = (
                f"sources cache {source_id} cleared removed={removed} "
                f"raw={stats['raw_files']} extracted={stats['extracted_files']} bytes={stats['total_bytes']}"
            )
            return CommandResult(state.with_updates(status_message=msg[:240]), msg)
        stats = cache.stats_for_source(source_id=source_id)
        msg = (
            f"sources cache {source_id} raw={stats['raw_files']} extracted={stats['extracted_files']} "
            f"bytes={stats['total_bytes']}"
        )
        return CommandResult(state.with_updates(status_message=msg[:240]), msg)
    return CommandResult(state, "sources: list | packs | pack show <id> | pack bootstrap <id> [--dry-run] | pack query <id> <question> | refresh <id> | snapshots <id> | cite <id> | cache <id> [clear]", handled=False)
