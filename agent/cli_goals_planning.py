"""'sources' and 'plan summary' CLI subcommand handlers (SPLIT-013).

Output goes through the agent.cli_goals facade (`_cli.*`) so that tests can
keep monkeypatching agent.cli_goals attributes.
"""

import json
import sys

from agent import cli_goals as _cli


def _handle_sources_command(subcommand: str, extra: list[str], args) -> int:
    from agent.sources.source_pack_service import SourcePackService

    service = SourcePackService()
    if subcommand == "list-packs":
        packs = service.list_packs()
        if not packs:
            _cli._print_terminal("No source packs found")
            return 0
        for pack in packs:
            _cli._print_terminal("{}\t{}", pack.get("source_pack_id", "-"), pack.get("display_name", "-"))
        return 0
    if subcommand == "bootstrap":
        source_pack_id = str(extra[0]).strip() if extra else ""
        if not source_pack_id:
            print("Error: 'sources bootstrap' requires <source_pack_id>", file=sys.stderr)
            return 2
        result = service.bootstrap(
            source_pack_id=source_pack_id,
            dry_run=bool(getattr(args, "dry_run", False)),
            skip_source_ids=list(getattr(args, "skip_source", []) or []),
            include_optional=bool(getattr(args, "include_optional_sources", False)),
        )
        _cli._print_terminal("status: {}", result.get("status", "unknown"))
        _cli._print_terminal("source_pack_id: {}", result.get("source_pack_id", "-"))
        _cli._print_terminal("selected_sources: {}", len(list(result.get("selected_sources") or [])))
        if result.get("skip_source_ids"):
            _cli._print_terminal("skipped: {}", ", ".join(list(result.get("skip_source_ids") or [])))
        for warning in list(result.get("warnings") or []):
            _cli._print_terminal("warning: {}", warning)
        if str(result.get("status") or "") == "ok":
            bundle = dict(result.get("codecompass_bundle") or {})
            _cli._print_terminal("snapshots: {}", ", ".join(list(result.get("snapshot_ids") or [])) or "-")
            _cli._print_terminal("bundle_id: {}", bundle.get("bundle_id", "-"))
            _cli._print_terminal("bundle_path: {}", bundle.get("bundle_path", "-"))
        if list(dict(result.get("license_policy_report") or {}).get("warnings") or []):
            for item in list(dict(result.get("license_policy_report") or {}).get("warnings") or []):
                _cli._print_terminal("license-warning: {}", item)
        if list(dict(result.get("license_policy_report") or {}).get("blocking_errors") or []):
            for item in list(dict(result.get("license_policy_report") or {}).get("blocking_errors") or []):
                _cli._print_terminal("license-error: {}", item)
            return 1
        return 0
    if subcommand == "doctor":
        source_pack_id = str(extra[0]).strip() if extra else "ananta-dev-default"
        report = service.doctor(source_pack_id=source_pack_id)
        if bool(getattr(args, "json_output", False)):
            print(json.dumps(report, ensure_ascii=False))
            return 0 if bool(report.get("ready")) else 1
        _cli._print_terminal("status: {}", report.get("status", "unknown"))
        _cli._print_terminal("source_pack_id: {}", report.get("source_pack_id", "-"))
        _cli._print_terminal("bundle_ready: {}", "yes" if bool(report.get("bundle_ready")) else "no")
        for source_id, details in dict(report.get("sources") or {}).items():
            _cli._print_terminal(
                "{}\tregistered={}\tsnapshot={}\ttrust={}\tlicense={}",
                source_id,
                "yes" if bool(dict(details).get("registered")) else "no",
                dict(details).get("snapshot_status", "-"),
                dict(details).get("trust_level", "-"),
                dict(details).get("license_ref", "-"),
            )
        for step in list(report.get("next_steps") or []):
            _cli._print_terminal("next-step: {}", step)
        return 0
    if subcommand == "query":
        source_pack_id = str(extra[0]).strip() if extra else ""
        query = " ".join(extra[1:]).strip() if len(extra) > 1 else ""
        if not source_pack_id or not query:
            print("Error: 'sources query' requires <source_pack_id> <query text>", file=sys.stderr)
            return 2
        result = service.answer_preview(source_pack_id=source_pack_id, query=query)
        if bool(getattr(args, "json_output", False)):
            print(json.dumps(result, ensure_ascii=False))
            return 0
        _cli._print_terminal("status: {}", result.get("status", "unknown"))
        _cli._print_terminal("source_pack_id: {}", result.get("source_pack_id", "-"))
        _cli._print_terminal("origins: {}", ", ".join(list(result.get("origins") or [])) or "-")
        _cli._print_terminal("codecompass_bundle_id: {}", result.get("codecompass_bundle_id", "-"))
        _cli._print_terminal("context_hash: {}", result.get("context_hash", "-"))
        for ref in list(result.get("source_references") or []):
            _cli._print_terminal(
                "source_ref: pack={} source_id={} snapshot_id={} trust_level={} bundle={}",
                ref.get("source_pack_id", "-"),
                ref.get("source_id", "-"),
                ref.get("snapshot_id", "-"),
                ref.get("trust_level", "-"),
                ref.get("codecompass_bundle_id", "-"),
            )
        return 0
    print(f"Error: unknown sources subcommand '{subcommand}'", file=sys.stderr)
    return 2


def _handle_plan_command(subcommand: str, extra: list[str], args) -> int:
    from agent.services.planning_summary_doctor_service import doctor_file, fix_file, migrate_track_todos

    if subcommand != "summary":
        print(f"Error: unknown plan subcommand '{subcommand}'", file=sys.stderr)
        return 2
    action = str(extra[0]).strip().lower() if extra else ""
    if action == "doctor":
        target = str(extra[1]).strip() if len(extra) > 1 else ""
        if not target:
            print("Error: 'plan summary doctor' requires <file>", file=sys.stderr)
            return 2
        result = doctor_file(target)
        if bool(getattr(args, "json_output", False)):
            print(json.dumps(result, ensure_ascii=False))
        else:
            _cli._print_terminal("status: {}", "ok" if bool(result.get("valid")) else "invalid")
            _cli._print_terminal("path: {}", result.get("path", "-"))
            _cli._print_terminal("format: {}", result.get("format", "-"))
            _cli._print_terminal("summary_recalculation_status: {}", result.get("summary_recalculation_status", "-"))
            _cli._print_terminal("repaired_fields: {}", ", ".join(list(result.get("repaired_fields") or [])) or "-")
            for issue in list(result.get("issues") or []):
                _cli._print_terminal(
                    "issue: path={} reason={} message={}",
                    dict(issue).get("path", "-"),
                    dict(issue).get("reason_code", "-"),
                    dict(issue).get("human_message", "-"),
                )
        return 0 if bool(result.get("valid")) else 1
    if action == "fix":
        target = str(extra[1]).strip() if len(extra) > 1 else ""
        if not target:
            print("Error: 'plan summary fix' requires <file>", file=sys.stderr)
            return 2
        write = bool(getattr(args, "write", False))
        result = fix_file(target, write=write)
        if bool(getattr(args, "json_output", False)):
            print(json.dumps({k: v for k, v in result.items() if k != "payload"}, ensure_ascii=False))
        else:
            _cli._print_terminal("status: {}", "ok" if bool(result.get("valid")) else "invalid")
            _cli._print_terminal("path: {}", result.get("path", "-"))
            _cli._print_terminal("write: {}", "yes" if write else "no (dry-run)")
            _cli._print_terminal("changed: {}", "yes" if bool(result.get("changed")) else "no")
            _cli._print_terminal("repaired_fields: {}", ", ".join(list(result.get("repaired_fields") or [])) or "-")
            for issue in list(result.get("issues") or []):
                _cli._print_terminal(
                    "issue: path={} reason={} message={}",
                    dict(issue).get("path", "-"),
                    dict(issue).get("reason_code", "-"),
                    dict(issue).get("human_message", "-"),
                )
        return 0 if bool(result.get("valid")) else 1
    if action == "migrate":
        repo_root = str(extra[1]).strip() if len(extra) > 1 else "."
        dry_run = bool(getattr(args, "dry_run", False) or not bool(getattr(args, "write", False)))
        report = migrate_track_todos(
            repo_root=repo_root,
            dry_run=dry_run,
            convert_epics=bool(getattr(args, "convert_epics", False)),
        )
        if bool(getattr(args, "json_output", False)):
            print(json.dumps(report, ensure_ascii=False))
            return 0
        _cli._print_terminal("repo_root: {}", report.get("repo_root", "-"))
        _cli._print_terminal("dry_run: {}", "yes" if bool(report.get("dry_run")) else "no")
        _cli._print_terminal("convert_epics: {}", "yes" if bool(report.get("convert_epics")) else "no")
        _cli._print_terminal("scanned: {} track_files: {} changed: {}", report.get("scanned", 0), report.get("track_files", 0), report.get("changed", 0))
        for item in list(report.get("results") or [])[:50]:
            _cli._print_terminal(
                "track: {} changed={} legacy_epics={} repaired_fields={}{}",
                dict(item).get("path", "-"),
                "yes" if bool(dict(item).get("changed")) else "no",
                "yes" if bool(dict(item).get("legacy_epics_detected")) else "no",
                ", ".join(list(dict(item).get("repaired_fields") or [])) or "-",
                f" warning={dict(item).get('warning')}" if dict(item).get("warning") else "",
            )
        return 0
    print("Error: 'plan summary' requires doctor|fix|migrate", file=sys.stderr)
    return 2
