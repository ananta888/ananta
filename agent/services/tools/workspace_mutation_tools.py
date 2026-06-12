"""AWWPI-009/010/011: hub-validated workspace mutation tools.

``repo.apply_patch`` applies a single-file unified diff atomically: the
patch is validated against ``expected_old_hash`` (or the hunk context)
and applied in memory first — conflicts return ``rejected_reason``
instead of half-applied files. ``repo.write_file`` separates
``create_only`` from ``replace_existing`` (hash-checked), bounds file
size and refuses binary replacements. ``workspace.diff`` returns the
bounded diff against the hub baseline together with the mutation policy
result so it can feed the next feedback iteration as evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agent.services.tools._evidence import (
    EVIDENCE_KIND_DIFF,
    EVIDENCE_KIND_POLICY,
    build_evidence_entry,
    build_tool_result,
)
from agent.services.tools.repo_tools import WorkspacePathError, resolve_workspace_path
from agent.services.generated_source_line_policy_service import (
    DECISION_BLOCKED,
    extract_policy_config,
    get_generated_source_line_policy_service,
)

_DEFAULT_MAX_WRITE_BYTES = 262144
_DEFAULT_MAX_REPLACE_EXISTING_BYTES = 65536
_DEFAULT_MAX_REPLACE_RANGE_LINES = 120


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_line_warning(result: dict[str, Any]) -> str | None:
    status = str(result.get("status") or "")
    if status and status != "ok":
        return f"source_line_policy_{status}"
    return None


def _build_source_line_evidence(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    blocked = [
        str(row.get("path") or "")
        for row in list(result.get("file_results") or [])
        if isinstance(row, dict) and str(row.get("decision") or "") == DECISION_BLOCKED
    ]
    excerpt = f"status={result.get('status')}; summary={summary}; blocked={blocked}"
    entry, _ = build_evidence_entry(kind=EVIDENCE_KIND_POLICY, path=".", excerpt=excerpt, max_excerpt_chars=2000)
    return entry


def _parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
    """Parse the hunks of a single-file unified diff.

    Returns a list of hunks: {old_start, old_lines: [..], new_lines: [..]}.
    Raises ValueError on structural problems.
    """
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(diff_text or "").splitlines():
        if raw_line.startswith(("---", "+++", "diff ", "index ")):
            continue
        if raw_line.startswith("@@"):
            try:
                header = raw_line.split("@@")[1].strip()
                old_part = header.split(" ")[0]
                old_start = int(old_part.lstrip("-").split(",")[0])
            except (IndexError, ValueError) as exc:
                raise ValueError(f"invalid_hunk_header:{raw_line}") from exc
            current = {"old_start": max(1, old_start), "old_lines": [], "new_lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue
        if raw_line.startswith("+"):
            current["new_lines"].append(raw_line[1:])
        elif raw_line.startswith("-"):
            current["old_lines"].append(raw_line[1:])
        elif raw_line.startswith(" ") or raw_line == "":
            text = raw_line[1:] if raw_line.startswith(" ") else ""
            current["old_lines"].append(text)
            current["new_lines"].append(text)
        elif raw_line.startswith("\\"):
            continue
        else:
            raise ValueError(f"invalid_diff_line:{raw_line[:60]}")
    if not hunks:
        raise ValueError("no_hunks_found")
    return hunks


def _apply_hunks(original_lines: list[str], hunks: list[dict[str, Any]]) -> list[str]:
    """Apply hunks in memory; raises ValueError('hunk_context_mismatch') on conflict."""
    result = list(original_lines)
    offset = 0
    for hunk in hunks:
        start = hunk["old_start"] - 1 + offset
        old_lines = hunk["old_lines"]
        if result[start : start + len(old_lines)] != old_lines:
            # Tolerate a small drift window before rejecting.
            found = None
            for delta in range(-20, 21):
                idx = start + delta
                if idx < 0:
                    continue
                if result[idx : idx + len(old_lines)] == old_lines:
                    found = idx
                    break
            if found is None:
                raise ValueError("hunk_context_mismatch")
            start = found
        result[start : start + len(old_lines)] = hunk["new_lines"]
        offset += len(hunk["new_lines"]) - len(old_lines)
    return result


def _replace_range(
    original_text: str,
    *,
    line_start: int,
    line_end: int,
    replacement: str,
    max_lines: int,
) -> str:
    if line_start < 1 or line_end < line_start:
        raise ValueError("invalid_line_range")
    if (line_end - line_start + 1) > max_lines:
        raise ValueError("replace_range_too_large")
    original_lines = original_text.splitlines()
    if line_start > len(original_lines) + 1:
        raise ValueError("line_start_out_of_bounds")
    patched = list(original_lines)
    patched[line_start - 1 : min(line_end, len(original_lines))] = str(replacement or "").splitlines()
    return "\n".join(patched) + ("\n" if original_text.endswith("\n") else "")


def repo_apply_patch(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    cfg = dict(config or {})
    target = str(args.get("target_path") or "").strip()
    diff_text = str(args.get("unified_diff") or "")
    try:
        path = resolve_workspace_path(workspace_dir, target)
    except WorkspacePathError as exc:
        return build_tool_result(
            tool_name="repo.apply_patch",
            tool_call_id=tool_call_id,
            status="rejected",
            risk_class="write",
            error=str(exc),
            data={"applied": False, "rejected_reason": str(exc)},
        )
    if not path.is_file():
        return build_tool_result(
            tool_name="repo.apply_patch",
            tool_call_id=tool_call_id,
            status="rejected",
            risk_class="write",
            error="target_file_not_found",
            data={"applied": False, "rejected_reason": "target_file_not_found"},
        )
    original_text = path.read_text(encoding="utf-8", errors="replace")
    expected_old_hash = str(args.get("expected_old_hash") or "").strip()
    if expected_old_hash and expected_old_hash != _sha256_text(original_text):
        return build_tool_result(
            tool_name="repo.apply_patch",
            tool_call_id=tool_call_id,
            status="rejected",
            risk_class="write",
            error="expected_old_hash_mismatch",
            data={"applied": False, "rejected_reason": "expected_old_hash_mismatch"},
        )
    try:
        variant = str(args.get("variant") or ("replace_range" if args.get("line_start") is not None else "unified_diff")).strip().lower()
        if variant == "replace_range":
            replacement = args.get("replacement") if args.get("replacement") is not None else args.get("content")
            patched_text = _replace_range(
                original_text,
                line_start=int(args.get("line_start") or 0),
                line_end=int(args.get("line_end") or 0),
                replacement=str(replacement or ""),
                max_lines=max(1, min(int(cfg.get("max_replace_range_lines") or _DEFAULT_MAX_REPLACE_RANGE_LINES), _DEFAULT_MAX_REPLACE_RANGE_LINES)),
            )
            diff_excerpt = f"replace_range {target}:{args.get('line_start')}-{args.get('line_end')}\n{str(replacement or '')[:3500]}"
        else:
            keep_trailing_newline = original_text.endswith("\n")
            hunks = _parse_unified_diff(diff_text)
            patched_lines = _apply_hunks(original_text.splitlines(), hunks)
            patched_text = "\n".join(patched_lines) + ("\n" if keep_trailing_newline else "")
            diff_excerpt = diff_text[:4000]
    except ValueError as exc:
        return build_tool_result(
            tool_name="repo.apply_patch",
            tool_call_id=tool_call_id,
            status="rejected",
            risk_class="write",
            error=str(exc),
            data={"applied": False, "rejected_reason": str(exc)},
        )
    rel = str(path.relative_to(Path(workspace_dir).resolve())).replace("\\", "/")
    baseline = {rel: len(original_text.splitlines())}
    path.write_text(patched_text, encoding="utf-8")
    source_line_result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=workspace_dir,
        changed_rel_paths=[rel],
        cfg=extract_policy_config(cfg),
        baseline=baseline,
    ).as_dict()
    source_line_warning = _source_line_warning(source_line_result)
    if source_line_result.get("status") == DECISION_BLOCKED:
        path.write_text(original_text, encoding="utf-8")
        return build_tool_result(
            tool_name="repo.apply_patch",
            tool_call_id=tool_call_id,
            status="policy_blocked",
            risk_class="write",
            evidence=[_build_source_line_evidence(source_line_result)],
            error="source_line_policy_blocked",
            data={
                "applied": False,
                "changed_files": [rel],
                "rejected_reason": "source_line_policy_blocked",
                "source_line_policy_result": source_line_result,
                "variant": variant,
            },
            warnings=[source_line_warning] if source_line_warning else None,
        )
    entry, _ = build_evidence_entry(kind=EVIDENCE_KIND_DIFF, path=rel, excerpt=diff_excerpt, max_excerpt_chars=4000)
    evidence = [entry]
    if source_line_result.get("enabled"):
        evidence.append(_build_source_line_evidence(source_line_result))
    return build_tool_result(
        tool_name="repo.apply_patch",
        tool_call_id=tool_call_id,
        status="ok",
        risk_class="write",
        evidence=evidence,
        data={
            "applied": True,
            "changed_files": [rel],
            "diff_excerpt": diff_excerpt,
            "content_hashes": {rel: _sha256_text(patched_text)},
            "variant": variant,
            "reason": str(args.get("reason") or ""),
            "source_line_policy_result": source_line_result,
        },
        warnings=[source_line_warning] if source_line_warning else None,
    )


def repo_write_file(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    cfg = dict(config or {})
    max_bytes = max(1024, min(int(cfg.get("max_write_file_bytes") or _DEFAULT_MAX_WRITE_BYTES), 10 * 1024 * 1024))
    max_replace_existing_bytes = max(
        1024,
        min(
            int(cfg.get("max_replace_existing_bytes") or _DEFAULT_MAX_REPLACE_EXISTING_BYTES),
            _DEFAULT_MAX_REPLACE_EXISTING_BYTES,
        ),
    )
    mode = str(args.get("mode") or "create_only").strip().lower()
    content = args.get("content")
    if not isinstance(content, str):
        return build_tool_result(
            tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
            error="content_must_be_text", data={"applied": False, "rejected_reason": "content_must_be_text"},
        )
    if len(content.encode("utf-8")) > max_bytes:
        return build_tool_result(
            tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
            error="content_too_large", data={"applied": False, "rejected_reason": "content_too_large"},
        )
    try:
        path = resolve_workspace_path(workspace_dir, args.get("path"))
    except WorkspacePathError as exc:
        return build_tool_result(
            tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
            error=str(exc), data={"applied": False, "rejected_reason": str(exc)},
        )
    if mode not in {"create_only", "replace_existing"}:
        return build_tool_result(
            tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
            error=f"unknown_mode:{mode}", data={"applied": False, "rejected_reason": f"unknown_mode:{mode}"},
        )
    if mode == "create_only" and path.exists():
        return build_tool_result(
            tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
            error="file_already_exists", data={"applied": False, "rejected_reason": "file_already_exists"},
        )
    original_text: str | None = None
    existed_before = path.exists()
    if mode == "replace_existing":
        if not path.is_file():
            return build_tool_result(
                tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
                error="file_not_found", data={"applied": False, "rejected_reason": "file_not_found"},
            )
        payload = path.read_bytes()
        if len(payload) > max_replace_existing_bytes:
            return build_tool_result(
                tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
                error="replace_existing_file_too_large",
                data={"applied": False, "rejected_reason": "replace_existing_file_too_large"},
            )
        if b"\x00" in payload:
            return build_tool_result(
                tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
                error="binary_file_replace_blocked", data={"applied": False, "rejected_reason": "binary_file_replace_blocked"},
            )
        expected_old_hash = str(args.get("expected_old_hash") or "").strip()
        approved = bool(args.get("hub_approved"))
        if not expected_old_hash and not approved:
            return build_tool_result(
                tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
                error="replace_requires_expected_old_hash_or_approval",
                data={"applied": False, "rejected_reason": "replace_requires_expected_old_hash_or_approval"},
            )
        if expected_old_hash and expected_old_hash != hashlib.sha256(payload).hexdigest():
            return build_tool_result(
                tool_name="repo.write_file", tool_call_id=tool_call_id, status="rejected", risk_class="write",
                error="expected_old_hash_mismatch",
                data={"applied": False, "rejected_reason": "expected_old_hash_mismatch"},
            )
        original_text = payload.decode("utf-8", errors="replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    rel = str(path.relative_to(Path(workspace_dir).resolve())).replace("\\", "/")
    baseline = {rel: len(original_text.splitlines()) if original_text is not None else None}
    path.write_text(content, encoding="utf-8")
    source_line_result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=workspace_dir,
        changed_rel_paths=[rel],
        cfg=extract_policy_config(cfg),
        baseline=baseline,
    ).as_dict()
    source_line_warning = _source_line_warning(source_line_result)
    if source_line_result.get("status") == DECISION_BLOCKED:
        if existed_before and original_text is not None:
            path.write_text(original_text, encoding="utf-8")
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return build_tool_result(
            tool_name="repo.write_file",
            tool_call_id=tool_call_id,
            status="policy_blocked",
            risk_class="write",
            evidence=[_build_source_line_evidence(source_line_result)],
            error="source_line_policy_blocked",
            data={
                "applied": False,
                "mode": mode,
                "changed_files": [rel],
                "rejected_reason": "source_line_policy_blocked",
                "source_line_policy_result": source_line_result,
            },
            warnings=[source_line_warning] if source_line_warning else None,
        )
    return build_tool_result(
        tool_name="repo.write_file",
        tool_call_id=tool_call_id,
        status="ok",
        risk_class="write",
        data={
            "applied": True,
            "mode": mode,
            "changed_files": [rel],
            "content_hashes": {rel: _sha256_text(content)},
            "source_line_policy_result": source_line_result,
        },
        evidence=[_build_source_line_evidence(source_line_result)] if source_line_result.get("enabled") else None,
        warnings=[source_line_warning] if source_line_warning else None,
    )


def workspace_diff(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """AWWPI-011: diff vs. baseline plus mutation policy result as evidence."""
    cfg = dict(config or {})
    max_diff_chars = max(500, min(int(cfg.get("max_diff_chars") or 12000), 100000))
    from agent.services.worker_workspace_service import get_worker_workspace_service

    svc = get_worker_workspace_service()
    workspace = Path(workspace_dir).resolve()
    changed = svc.detect_changed_files_against_interactive_baseline(workspace_dir=workspace)
    meaningful = svc.filter_meaningful_changed_files(changed)
    diff_text, diff_truncated = svc.build_workspace_diff_text(
        workspace_dir=workspace, changed_rel_paths=meaningful, max_chars=max_diff_chars
    )

    from agent.services.ananta_workspace_mutation_policy import get_ananta_workspace_mutation_policy_service

    policy_result = get_ananta_workspace_mutation_policy_service().evaluate_changed_files(
        workspace_dir=workspace,
        changed_rel_paths=meaningful,
        materialization_manifest=cfg.get("materialization_manifest"),
        allowed_new_file_globs=list(cfg.get("allowed_new_file_globs") or []),
        require_materialized_scope=bool(cfg.get("require_materialized_scope", True)),
    )
    source_line_result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=workspace,
        changed_rel_paths=meaningful,
        cfg=extract_policy_config(cfg),
        baseline=None,
    ).as_dict()
    diff_entry, _ = build_evidence_entry(
        kind=EVIDENCE_KIND_DIFF, path=".", excerpt=diff_text or "(no meaningful diff)", max_excerpt_chars=max_diff_chars
    )
    policy_entry, _ = build_evidence_entry(
        kind=EVIDENCE_KIND_POLICY,
        path=".",
        excerpt=(
            f"status={policy_result.status}; allowed={policy_result.allowed_changes}; "
            f"blocked={[row['path'] for row in policy_result.blocked_changes]}; "
            f"questionable={[row['path'] for row in policy_result.questionable_changes]}"
        ),
        max_excerpt_chars=2000,
    )
    warnings = list(policy_result.warnings)
    source_line_warning = _source_line_warning(source_line_result)
    if source_line_warning:
        warnings.append(source_line_warning)
    if diff_truncated:
        warnings.append("diff_truncated_see_artifact")
    return build_tool_result(
        tool_name="workspace.diff",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=[diff_entry, policy_entry],
        data={
            "changed_files": meaningful,
            "policy_result": policy_result.as_dict(),
            "source_line_policy_result": source_line_result,
            "diff_truncated": diff_truncated,
        },
        warnings=warnings,
    )
