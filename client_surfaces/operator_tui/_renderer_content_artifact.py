"""Internal sub-module of the Operator TUI renderer.

Extracted from _renderer_content.py to keep the main module small.
This module owns: Artifact sub-mode renderers: planning track, helpcenter,
mail, goal artifacts, diff3.

Public re-exports: the parent _renderer_content module re-exports every
function so the public chain (renderer -> _renderer_content -> sub-module)
keeps working transparently.
"""

from __future__ import annotations

import re

from client_surfaces.operator_tui.goal_artifact_filters import filter_goal_artifact_view
from client_surfaces.operator_tui._renderer_utils import _clip

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _planning_track_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    goal_id = str(payload.get("goal_id") or "unknown")
    status = str(payload.get("planning_status") or "idle")
    lifecycle = [str(item) for item in list(payload.get("planning_lifecycle") or []) if str(item).strip()]
    selected_track = dict(payload.get("selected_track") or {})
    selected_output = str(payload.get("selected_output_id") or "")
    active_output = str(payload.get("active_output_id") or "")
    filters = dict(payload.get("task_filters") or {})
    warnings = list(selected_track.get("quality_gate_warnings") or [])
    rows = list(payload.get("track_rows") or [])

    lines = [
        f"  Planning Track: {goal_id}",
        f"  Status: {status}  lifecycle={' -> '.join(lifecycle) if lifecycle else '-'}",
        f"  Selected output: {selected_output or '-'}  active={active_output or '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
    if not selected_track:
        if rows:
            lines.append("  planning outputs available, but selected track payload missing")
        else:
            lines.append("  no planning track outputs")
        return lines

    owner = str(selected_track.get("owner") or "-")
    track = str(selected_track.get("track") or "-")
    goal = str(selected_track.get("goal") or goal_id)
    progress = dict(selected_track.get("progress_summary") or {})
    summary = dict(selected_track.get("tasks_status_summary") or {})
    weighted = dict(selected_track.get("weighted_progress_summary") or {})
    metadata = dict(selected_track.get("derived_summary_metadata") or {})
    type_summary = dict(selected_track.get("tasks_type_summary") or {})
    provenance = dict(selected_track.get("provenance") or {})
    mapping = dict(selected_track.get("task_mapping") or {})
    source_refs = [str(item) for item in list(selected_track.get("source_references") or []) if str(item).strip()]
    context_refs = [str(item) for item in list(selected_track.get("context_references") or []) if str(item).strip()]
    raw_summary_status = str(selected_track.get("summary_recalculation_status") or "not_needed")
    summary_status = (
        "repaired"
        if raw_summary_status == "repaired"
        else ("invalid" if raw_summary_status == "failed" else "fresh")
    )
    repaired_fields = [str(item) for item in list(selected_track.get("repaired_fields") or []) if str(item).strip()]
    lines.append(f"  Header: owner={owner} track={track} goal={goal}")
    lines.append(
        _clip(
            "  Summary: "
            f"state={progress.get('state') or '-'} done={summary.get('by_status', {}).get('done', 0)} "
            f"todo={summary.get('by_status', {}).get('todo', 0)} total={summary.get('total', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Progress: "
            f"count_based={progress.get('count_based_percent', '-')}% "
            f"weighted={progress.get('weighted_percent', '-')}% "
            f"blocked_count={summary.get('by_status', {}).get('blocked', 0)} "
            f"blocked_weight={weighted.get('blocked_weight', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Critical path: "
            f"done={summary.get('critical_path', {}).get('done', 0)}/"
            f"{summary.get('critical_path', {}).get('total', 0)} "
            f"remaining={summary.get('critical_path', {}).get('remaining', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Derived summary: "
            f"status={summary_status} "
            f"source_hash={str(metadata.get('source_hash') or '-')[:12]} "
            f"repaired_fields={','.join(repaired_fields) if repaired_fields else '-'}",
            width,
        )
    )

    milestones = [dict(item) for item in list(selected_track.get("milestones") or []) if isinstance(item, dict)]
    lines.append("  [Milestones]")
    if not milestones:
        lines.append("    - none")
    for milestone in milestones[:8]:
        lines.append(
            _clip(
                f"    {milestone.get('id')} [{milestone.get('status')}] "
                f"{milestone.get('title')} tasks={','.join([str(x) for x in list(milestone.get('task_ids') or [])])}",
                width,
            )
        )

    tasks = [dict(item) for item in list(selected_track.get("tasks_filtered") or []) if isinstance(item, dict)]
    lines.append("  [Tasks]")
    if not tasks:
        lines.append("    - none (filtered)")
    for task in tasks[:16]:
        lines.append(
            _clip(
                f"    {task.get('id')} [{task.get('status')}] {task.get('priority')}/{task.get('risk')} "
                f"type={task.get('type') or '-'} {task.get('title')}",
                width,
            )
        )

    critical = [str(item) for item in list(selected_track.get("critical_path_tasks") or []) if str(item).strip()]
    lines.append(f"  Critical path tasks: {', '.join(critical) if critical else 'none'}")
    by_priority = dict(summary.get("by_priority") or {})
    by_risk = dict(summary.get("by_risk") or {})
    if by_priority:
        lines.append(_clip(f"  Priority breakdown: {', '.join([f'{k}={v}' for k, v in by_priority.items()])}", width))
    if by_risk:
        lines.append(_clip(f"  Risk breakdown: {', '.join([f'{k}={v}' for k, v in by_risk.items()])}", width))
    by_type = dict(type_summary.get("by_type") or {})
    if by_type:
        lines.append("  [Type progress]")
        for key in sorted(by_type.keys())[:8]:
            bucket = dict(by_type.get(key) or {})
            lines.append(
                _clip(
                    f"    {key}: total={bucket.get('total', 0)} done={bucket.get('done', 0)} "
                    f"partial={bucket.get('partial', 0)} blocked={bucket.get('blocked', 0)} "
                    f"progress={bucket.get('progress_percent', 0)}%",
                    width,
                )
            )
    if provenance:
        lines.append(
            _clip(
                f"  Provenance: {provenance.get('provenance_id') or '-'} model={dict(provenance.get('model_ref') or {}).get('model_id') or '-'}",
                width,
            )
        )
    lines.append(_clip(f"  Plan mapping: {len(mapping)} task refs", width))
    lines.append(_clip(f"  Sources: {len(source_refs)} refs  Context: {len(context_refs)} refs", width))

    if warnings:
        lines.append("  [Quality warnings]")
        for warning in warnings[:5]:
            if not isinstance(warning, dict):
                continue
            lines.append(_clip(f"    {warning.get('path')}: {warning.get('reason_code')}", width))

    status_issues = [dict(item) for item in list(payload.get("status_issues") or []) if isinstance(item, dict)]
    if status_issues:
        lines.append("  [Validation issues]")
        for issue in status_issues[:5]:
            lines.append(_clip(f"    {issue.get('path')}: {issue.get('reason_code')}", width))

    diff = dict(payload.get("plan_diff") or {})
    if diff:
        lines.append("  [Plan diff]")
        lines.append(
            f"    {diff.get('left_output_id')} -> {diff.get('right_output_id')} "
            f"new={len(list(diff.get('new_tasks') or []))} "
            f"changed={len(list(diff.get('changed_tasks') or []))} "
            f"removed={len(list(diff.get('removed_tasks') or []))}"
        )
    return lines


def _helpcenter_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    rows = [dict(item) for item in list(payload.get("reports") or []) if isinstance(item, dict)]
    selected_id = str(payload.get("selected_analysis_id") or "")
    selected_report = dict(payload.get("selected_report") or {})
    selected_analysis = dict(payload.get("selected_analysis") or {})
    last_ingest = dict(payload.get("last_ingest") or {})
    lines = [
        "  Helpcenter",
        f"  Reports: {len(rows)} selected={selected_id or '-'}",
    ]
    if last_ingest:
        lines.append(
            _clip(
                f"  Last ingest: repo={last_ingest.get('repo') or '-'} found={last_ingest.get('found', 0)} "
                f"written={last_ingest.get('written', 0)} dry_run={bool(last_ingest.get('dry_run'))}",
                width,
            )
        )
    if not rows:
        lines.append("  no helpcenter reports")
        return lines
    lines.append("  [Reports]")
    preview_rows = rows[:12] if not compact else rows[:6]
    for row in preview_rows:
        marker = "*" if str(row.get("analysis_id") or "") == selected_id else "-"
        lines.append(
            _clip(
                f"  {marker} {row.get('analysis_id')} [{row.get('status')}] "
                f"{row.get('severity')} {row.get('source_kind')} at {row.get('created_at')}",
                width,
            )
        )
    if not selected_report:
        return lines
    lines.append("  [Detail]")
    lines.append(
        _clip(
            f"  Source: kind={selected_report.get('source_kind') or '-'} "
            f"ref={selected_analysis.get('source_refs', ['-'])[0] if isinstance(selected_analysis.get('source_refs'), list) and selected_analysis.get('source_refs') else '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Summary: {selected_analysis.get('failure_summary') or '-'}", width))
    lines.append(
        _clip(
            f"  no_auto_fix={bool(selected_analysis.get('no_auto_fix'))} "
            f"md={selected_report.get('report_ref') or '-'} json={selected_report.get('json_ref') or '-'}",
            width,
        )
    )
    causes = [str(item) for item in list(selected_analysis.get("likely_causes") or []) if str(item).strip()]
    if causes:
        lines.append("  Likely causes:")
        for item in causes[:4]:
            lines.append(_clip(f"    - {item}", width))
    next_steps = [str(item) for item in list(selected_analysis.get("next_steps") or []) if str(item).strip()]
    if next_steps:
        lines.append("  Next steps:")
        for item in next_steps[:4]:
            lines.append(_clip(f"    - {item}", width))
    followup = str(payload.get("followup_suggestion") or "").strip()
    lines.append(_clip(f"  Follow-up suggestion: {followup or '-'}", width))
    return lines


def _mail_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    accounts = [dict(item) for item in list(payload.get("accounts") or []) if isinstance(item, dict)]
    selected_account_id = str(payload.get("selected_account_id") or "")
    mailboxes = [str(item) for item in list(payload.get("mailboxes") or []) if str(item).strip()]
    selected_mailbox = str(payload.get("selected_mailbox") or "")
    filters = dict(payload.get("filters") or {})
    rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
    total_messages = int(payload.get("total_messages") or 0)
    selected_key = str(payload.get("selected_message_key") or "")
    detail = dict(payload.get("selected_detail") or {})
    last_search_query = str(payload.get("last_search_query") or "")
    search_refs = [str(item) for item in list(payload.get("search_result_refs") or []) if str(item).strip()]
    notes = [dict(item) for item in list(payload.get("notes") or []) if isinstance(item, dict)]
    linked_goal_refs = [str(item) for item in list(payload.get("linked_goal_refs") or []) if str(item).strip()]
    current_artifact_ref = str(payload.get("current_artifact_ref") or "")
    lines = [
        "  Mail",
        f"  Accounts: {len(accounts)} selected={selected_account_id or '-'} mailbox={selected_mailbox or '-'}",
        f"  Mailboxes: {', '.join(mailboxes) if mailboxes else '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
        f"  Messages: showing={len(rows)} total={total_messages} offset={int(payload.get('list_offset') or 0)}",
        f"  Search: query={last_search_query or '-'} refs={len(search_refs)}",
        f"  Notes={len(notes)} linked-goals={len(linked_goal_refs)} artifacts={int(payload.get('artifact_count') or 0)}",
    ]
    if accounts:
        lines.append("  [Accounts]")
        for row in accounts[:6]:
            marker = "*" if str(row.get("account_id") or "") == selected_account_id else "-"
            lines.append(
                _clip(
                    f"  {marker} {row.get('display_name') or row.get('account_id')} "
                    f"state={row.get('state')} enabled={bool(row.get('enabled'))}",
                    width,
                )
            )
    if not rows:
        lines.append("  no mail messages")
        return lines
    lines.append("  [Mailbox list]")
    preview = rows[:8] if compact else rows[:14]
    for row in preview:
        ref = dict(row.get("message_ref") or {})
        header = dict(row.get("header_meta") or {})
        marker = "*" if str(ref.get("message_id") or "") == selected_key else "-"
        flags = []
        if bool(header.get("unread")):
            flags.append("unread")
        if bool(header.get("starred")):
            flags.append("starred")
        flags_text = ",".join(flags) or "-"
        lines.append(
            _clip(
                f"  {marker} uid={ref.get('uid')} date={ref.get('date')} from={ref.get('from')} "
                f"subject={header.get('subject') or '-'} flags={flags_text} "
                f"policy={row.get('body_scope') or 'metadata_only'} thread={row.get('thread_count') or 1}",
                width,
            )
        )
    if not detail:
        return lines
    lines.append("  [Detail]")
    detail_ref = dict(detail.get("message_ref") or {})
    detail_header = dict(detail.get("header_meta") or {})
    lines.append(_clip(f"  Message: id={detail_ref.get('message_id') or '-'} uid={detail_ref.get('uid') or '-'}", width))
    lines.append(_clip(f"  Subject: {detail_header.get('subject') or '-'}", width))
    lines.append(
        _clip(
            f"  Body loaded={bool(detail.get('body_loaded'))} "
            f"scope={detail.get('body_scope') or 'metadata_only'} "
            f"redaction={detail.get('redaction_status') or '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Artifact: {current_artifact_ref or '-'}", width))
    body_text = str(detail.get("body_text") or "").strip()
    lines.append(_clip(f"  Body preview: {body_text[:200] if body_text else '(not loaded)'}", width))
    attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, dict)]
    lines.append(f"  Attachments: {len(attachments)}")
    for attachment in attachments[:4]:
        lines.append(
            _clip(
                f"    - {attachment.get('filename') or '-'} "
                f"type={attachment.get('content_type') or '-'} "
                f"size={attachment.get('size') or 0} "
                f"danger={bool(attachment.get('danger'))}",
                width,
            )
        )
    downloaded = dict(detail.get("attachment_downloaded") or {})
    if downloaded:
        lines.append(
            _clip(
                f"  Last download: {downloaded.get('filename') or '-'} "
                f"sha256={str(downloaded.get('sha256') or '')[:16]}... "
                f"danger={bool(downloaded.get('dangerous'))}",
                width,
            )
        )
    return lines


def _goal_artifacts_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    def _safe(value: object) -> str:
        text = str(value or "")
        text = _ANSI_STRIP.sub("", text)
        return text.replace("\r", " ").replace("\n", " ")

    goal_id = str(payload.get("goal_id") or "unknown")
    filters = dict(payload.get("filters") or {})
    filtered = filter_goal_artifact_view(
        source_grants=list(payload.get("source_grants") or []),
        source_usages=list(payload.get("source_usages") or []),
        output_artifacts=list(payload.get("output_artifacts") or []),
        filters=filters,
    )
    grants = list(filtered.get("source_grants") or [])
    usages = list(filtered.get("source_usages") or [])
    outputs = list(filtered.get("output_artifacts") or [])
    usage_grant_ids = {_safe(item.get("grant_id") or "") for item in usages}
    lines = [
        f"  Goal Artifacts: {goal_id}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
        for grant in grants[:5]:
            grant_id = str(grant.get("grant_id") or "?")
            marker = "✓" if grant_id in usage_grant_ids else "~"
            lines.append(
                _clip(
                    f"  {marker} grant {grant_id} source={_safe(grant.get('artifact_ref') or '-')}",
                    width,
                )
            )
        for usage in usages[:5]:
            lines.append(_clip(f"  • usage {_safe(usage.get('usage_id'))} -> {_safe(usage.get('artifact_ref'))}", width))
        for output in outputs[:6]:
            provenance_note = " provenance-missing" if not _safe(output.get("provenance_id")) else ""
            lines.append(
                _clip(
                    "  ◦ output "
                    f"{_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                    f"status={_safe(output.get('status'))}{provenance_note} "
                    f"exec={_safe(output.get('execution_summary') or '')}",
                    width,
                )
            )
        if not grants and not usages and not outputs:
            lines.append("  (empty goal artifact graph)")
        return lines


    lines.append("  [Freigegeben]")
    if not grants:
        lines.append("    - none")
    for grant in grants[:8]:
        grant_id = _safe(grant.get("grant_id") or "?")
        used = grant_id in usage_grant_ids
        marker = "used" if used else "granted-not-used"
        lines.append(
            _clip(
                f"    {grant_id} [{marker}] sensitivity={_safe(grant.get('sensitivity'))} "
                f"boundary={_safe(grant.get('data_boundary'))} ref={_safe(grant.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Genutzt]")
    if not usages:
        lines.append("    - none")
    for usage in usages[:8]:
        lines.append(
            _clip(
                f"    {_safe(usage.get('usage_id'))} grant={_safe(usage.get('grant_id'))} "
                f"task={_safe(usage.get('task_id'))} worker={_safe(usage.get('worker_id'))} "
                f"ref={_safe(usage.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Erzeugt]")
    if not outputs:
        lines.append("    - none")
    for output in outputs[:10]:
        provenance_note = "provenance missing" if not _safe(output.get("provenance_id")) else f"prov={_safe(output.get('provenance_id'))}"
        lines.append(
            _clip(
                f"    {_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                f"status={_safe(output.get('status'))} task={_safe(output.get('task_id'))} "
                f"worker={_safe(output.get('worker_id'))} {provenance_note} created_at={_safe(output.get('created_at'))}",
                width,
            )
        )
        summary = _safe(output.get("execution_summary"))
        if summary:
            lines.append(_clip(f"      exec: {summary}", width))
    return lines


def _diff3_content_lines(payload: dict, *, width: int) -> list[str]:
    rows = list(payload.get("panel_summaries") or [])
    active_panel = str(payload.get("active_panel") or "A")
    sync = bool(payload.get("sync_scroll"))
    lines = [
        f"  DIFF3: active panel={active_panel} sync={'on' if sync else 'off'}",
    ]
    ai_state = dict(payload.get("ai_panel_state") or {})
    if ai_state:
        lines.append(
            _clip(
                f"  AI: mode={ai_state.get('mode')} status={ai_state.get('status')} "
                f"prompt={ai_state.get('prompt_template_ref')} last={ai_state.get('last_response_ref') or '-'}",
                width,
            )
        )
        findings = list(payload.get("raw_state", {}).get("extensions", {}).get("ai_last_findings") or [])
        if findings:
            lines.append(_clip(f"  AI findings: {findings[0]}", width))
    if not rows:
        lines.append("  (empty diff3 session)")
        return lines

    if width < 58:
        lines.append("  --- tabbed mode (<120 terminal width) ---")
        active = next((row for row in rows if str(row.get("panel_id") or "") == active_panel), rows[0])
        filters = dict(active.get("filters") or {})
        lines.append(
            _clip(
                f"  [{active.get('panel_id')}] {active.get('source_label')} "
                f"mode={active.get('render_mode')} status={active.get('status')}",
                width,
            )
        )
        if filters:
            lines.append(_clip(f"  filters: {', '.join(f'{k}={v}' for k, v in filters.items())}", width))
        stats = dict(active.get("stats") or {})
        if stats:
            lines.append(
                _clip(
                    f"  stats: files={stats.get('files',0)} hunks={stats.get('hunks',0)} truncated={stats.get('truncated',False)}",
                    width,
                )
            )
        return lines

    if width >= 84:
        cols = max(18, (width - 4) // 3)

        def _cell(text: str) -> str:
            return _clip(text, cols).ljust(cols)

        headers: list[str] = []
        details: list[str] = []
        filters_line: list[str] = []
        for row in rows[:3]:
            headers.append(_cell(f"[{row.get('panel_id')}] {row.get('source_label')}"))
            details.append(_cell(f"{row.get('render_mode')} | {row.get('status')}"))
            filters = dict(row.get("filters") or {})
            if filters:
                filters_line.append(_cell(",".join(f"{k}={v}" for k, v in filters.items())))
            else:
                filters_line.append(_cell("filters:none"))
        lines.append("  " + " | ".join(headers))
        lines.append("  " + " | ".join(details))
        lines.append("  " + " | ".join(filters_line))
        return lines

    lines.append("  --- compact diff3 view ---")
    for row in rows:
        filters = dict(row.get("filters") or {})
        filter_label = ",".join(f"{k}={v}" for k, v in filters.items()) if filters else "none"
        lines.append(
            _clip(
                f"  [{row.get('panel_id')}] {row.get('source_label')} "
                f"mode={row.get('render_mode')} status={row.get('status')} filters={filter_label}",
                width,
            )
        )
    return lines
