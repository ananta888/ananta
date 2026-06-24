"""RC-090: Operator-TUI Run-Control Commands.

Commands:
  :run status [<task_id>]              — show control-state
  :run pause <task_id>                 — pause task
  :run resume <task_id> [--instruction TEXT]
  :run cancel <task_id>
  :run retry <task_id>
  :run inject <task_id> <text>         — inject instruction
  :run inject <task_id> --mode pause_then_apply <text>

  :approval list [--status pending]    — list approval requests
  :approval grant <approval_id> [--task <task_id>] [--reason TEXT]
  :approval deny <approval_id> --reason TEXT [--task <task_id>]
  :approval status <approval_id>

  :branch list [<task_id>]             — list branch candidates
  :branch select <task_id> <branch_id> [--reason TEXT]
  :branch reject <task_id> <branch_id>

All mutating commands dispatch to Hub API only. Never communicate with Workers directly.
"""
from __future__ import annotations

import json
from typing import Any

from client_surfaces.operator_tui.models import CommandResult, OperatorState


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _api(state: OperatorState, method: str, path: str, body: dict | None = None) -> tuple[bool, Any]:
    """Minimal HTTP helper for TUI-to-Hub API calls."""
    import urllib.request
    import urllib.error

    endpoint = str(state.endpoint or "").rstrip("/")
    if not endpoint:
        return False, {"error": "no_hub_endpoint_configured"}
    url = f"{endpoint}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Content-Type", "application/json")
    if hasattr(state, "auth_token") and state.auth_token:
        req.add_header("Authorization", f"Bearer {state.auth_token}")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return True, payload
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode("utf-8"))
        except Exception:
            err = {"error": str(exc)}
        return False, err
    except Exception as exc:
        return False, {"error": str(exc)[:200]}


def _send_command(state: OperatorState, task_id: str, cmd_type: str, payload: dict) -> tuple[bool, dict]:
    ok, resp = _api(state, "POST", f"/api/tasks/{task_id}/commands", {
        "type": cmd_type,
        "payload": payload,
    })
    return ok, resp


# ── :run ──────────────────────────────────────────────────────────────────────

def handle_run_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "help"
    rest = args[1:]

    if sub in ("help", "?"):
        return CommandResult(
            state.with_updates(status_message="run: status|pause|resume|cancel|retry|inject"),
            "\n".join([
                ":run status [<task_id>]",
                ":run pause <task_id>",
                ":run resume <task_id> [--instruction TEXT]",
                ":run cancel <task_id>",
                ":run retry <task_id>",
                ":run inject <task_id> <text> [--mode next_iteration_instruction|pause_then_apply|context_note_only]",
            ]),
        )

    if sub == "status":
        task_id = rest[0] if rest else None
        if not task_id:
            return CommandResult(state.with_updates(status_message="run status: task_id required"), "run status: task_id required", handled=False)
        ok, resp = _api(state, "GET", f"/api/tasks/{task_id}/control-state")
        if not ok:
            msg = f"run status failed: {resp.get('error', resp)}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        cs = resp.get("control_state", {})
        lines = [
            f"task_id: {cs.get('task_id')}",
            f"task_status: {cs.get('task_status')}  run_status: {cs.get('run_status')}",
        ]
        approvals = cs.get("pending_approvals") or []
        if approvals:
            lines.append(f"pending approvals: {len(approvals)}")
            for a in approvals[:3]:
                lines.append(f"  {a.get('request_id', '')[:12]}  {a.get('tool_name')}  risk={a.get('risk_class')}")
        instr = cs.get("active_instruction")
        if instr:
            lines.append(f"active instruction: [{instr.get('instruction_class')}] {str(instr.get('text', ''))[:80]}")
        branches = cs.get("branches") or []
        if branches:
            lines.append(f"branches: {len(branches)}")
            for b in branches[:3]:
                lines.append(f"  {b.get('branch_id', '')[:12]}  {b.get('label')}  status={b.get('status')}")
        output = "\n".join(lines)
        return CommandResult(state.with_updates(status_message=f"run status: {cs.get('run_status')}"), output)

    if sub == "pause":
        if not rest:
            return CommandResult(state.with_updates(status_message="run pause: task_id required"), "run pause: task_id required", handled=False)
        task_id = rest[0]
        ok, resp = _send_command(state, task_id, "pause_run", {})
        return _cmd_result(state, "pause", task_id, ok, resp)

    if sub == "resume":
        if not rest:
            return CommandResult(state.with_updates(status_message="run resume: task_id required"), "run resume: task_id required", handled=False)
        task_id = rest[0]
        instr_parts, mode = _parse_instruction_args(rest[1:])
        payload: dict[str, Any] = {}
        if instr_parts:
            payload["instruction"] = " ".join(instr_parts)
            payload["mode"] = mode
        ok, resp = _send_command(state, task_id, "resume_run", payload)
        return _cmd_result(state, "resume", task_id, ok, resp)

    if sub == "cancel":
        if not rest:
            return CommandResult(state.with_updates(status_message="run cancel: task_id required"), "run cancel: task_id required", handled=False)
        task_id = rest[0]
        confirm = len(rest) > 1 and rest[1] in ("--yes", "-y", "--confirm")
        if not confirm:
            return CommandResult(
                state.with_updates(
                    status_message=f"Cancel task {task_id}? Bestätigen mit: :run cancel {task_id} --yes",
                ),
                f"Sicherheitsabfrage: :run cancel {task_id} --yes zum Bestätigen",
            )
        ok, resp = _send_command(state, task_id, "cancel_run", {})
        return _cmd_result(state, "cancel", task_id, ok, resp)

    if sub == "retry":
        if not rest:
            return CommandResult(state.with_updates(status_message="run retry: task_id required"), "run retry: task_id required", handled=False)
        task_id = rest[0]
        ok, resp = _send_command(state, task_id, "retry_run_or_task", {})
        return _cmd_result(state, "retry", task_id, ok, resp)

    if sub == "inject":
        if len(rest) < 2:
            return CommandResult(state.with_updates(status_message="run inject: task_id + text required"), "run inject: task_id <text>", handled=False)
        task_id = rest[0]
        text_parts, mode = _parse_instruction_args(rest[1:])
        if not text_parts:
            return CommandResult(state.with_updates(status_message="run inject: text required"), "run inject: text required", handled=False)
        text = " ".join(text_parts)
        ok, resp = _send_command(state, task_id, "inject_instruction", {
            "text": text,
            "mode": mode,
            "instruction_class": "constraint",
        })
        return _cmd_result(state, "inject", task_id, ok, resp)

    return CommandResult(state.with_updates(status_message=f"run: unbekanntes Sub-Kommando '{sub}'"), f"run {sub}: unbekannt. Hilfe: :run help", handled=False)


# ── :approval ────────────────────────────────────────────────────────────────

def handle_approval_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "list"
    rest = args[1:]

    if sub in ("help", "?"):
        return CommandResult(
            state.with_updates(status_message="approval: list|grant|deny|status"),
            "\n".join([
                ":approval list [--status pending]",
                ":approval grant <approval_id> [--task <task_id>] [--reason TEXT]",
                ":approval deny <approval_id> --reason TEXT [--task <task_id>]",
                ":approval status <approval_id>",
            ]),
        )

    if sub == "list":
        status = "pending"
        for i, arg in enumerate(rest):
            if arg == "--status" and i + 1 < len(rest):
                status = rest[i + 1]
        ok, resp = _api(state, "GET", f"/api/approvals?status={status}")
        if not ok:
            msg = f"approval list failed: {resp.get('error', resp)}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        requests = resp.get("requests") or []
        if not requests:
            return CommandResult(state.with_updates(status_message="keine Approval-Requests"), "Keine Approval-Requests gefunden.")
        lines = [f"Approval-Requests (status={status}): {len(requests)}"]
        for r in requests[:10]:
            lines.append(
                f"  {str(r.get('request_id', ''))[:12]}  {r.get('tool_name')}  "
                f"risk={r.get('risk_class')}  status={r.get('status')}  "
                f"task={str(r.get('task_id') or '')[:12]}"
            )
        if len(requests) > 10:
            lines.append(f"  ... {len(requests) - 10} weitere")
        output = "\n".join(lines)
        return CommandResult(state.with_updates(status_message=f"{len(requests)} Approval-Requests"), output)

    if sub == "grant":
        if not rest:
            return CommandResult(state.with_updates(status_message="approval grant: approval_id required"), "approval grant: approval_id required", handled=False)
        approval_id = rest[0]
        task_id, reason, remaining = _parse_task_reason_args(rest[1:])
        if not task_id:
            return CommandResult(
                state.with_updates(status_message=f"approval grant {approval_id}: --task <task_id> required"),
                "approval grant benötigt --task <task_id>",
                handled=False,
            )
        ok, resp = _send_command(state, task_id, "approve_gate", {
            "approval_id": approval_id,
            "reason": reason or "Operator via TUI",
        })
        if not ok or (resp.get("command", {}) or {}).get("status") == "failed":
            msg = _extract_error(resp)
            return CommandResult(state.with_updates(status_message=f"approval grant failed: {msg}"), f"approval grant {approval_id[:12]}: {msg}", handled=False)
        cmd = resp.get("command", {})
        msg = f"approved: {approval_id[:12]} → {cmd.get('result', {}).get('status', 'granted')}"
        return CommandResult(state.with_updates(status_message=msg), msg)

    if sub == "deny":
        if not rest:
            return CommandResult(state.with_updates(status_message="approval deny: approval_id required"), "approval deny: approval_id required", handled=False)
        approval_id = rest[0]
        task_id, reason, _ = _parse_task_reason_args(rest[1:])
        if not reason:
            return CommandResult(state.with_updates(status_message="approval deny: --reason required"), "approval deny benötigt --reason TEXT", handled=False)
        if not task_id:
            return CommandResult(state.with_updates(status_message="approval deny: --task required"), "approval deny benötigt --task <task_id>", handled=False)
        ok, resp = _send_command(state, task_id, "deny_gate", {
            "approval_id": approval_id,
            "reason": reason,
        })
        if not ok or (resp.get("command", {}) or {}).get("status") == "failed":
            msg = _extract_error(resp)
            return CommandResult(state.with_updates(status_message=f"approval deny failed: {msg}"), f"approval deny {approval_id[:12]}: {msg}", handled=False)
        msg = f"denied: {approval_id[:12]}"
        return CommandResult(state.with_updates(status_message=msg), msg)

    if sub == "status":
        if not rest:
            return CommandResult(state.with_updates(status_message="approval status: approval_id required"), "approval status: approval_id required", handled=False)
        approval_id = rest[0]
        ok, resp = _api(state, "GET", f"/api/approvals/{approval_id}")
        if not ok:
            msg = f"approval status failed: {resp.get('error', resp)}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        lines = [
            f"request_id: {resp.get('request_id')}",
            f"tool: {resp.get('tool_name')}  risk: {resp.get('risk_class')}  status: {resp.get('status')}",
            f"task: {resp.get('task_id')}  digest: {resp.get('digest_prefix')}…",
        ]
        if resp.get("decision_reason"):
            lines.append(f"reason: {resp.get('decision_reason')}")
        return CommandResult(state.with_updates(status_message=f"approval {resp.get('status')}"), "\n".join(lines))

    return CommandResult(state.with_updates(status_message=f"approval: unbekannt '{sub}'"), f"approval {sub}: unbekannt. Hilfe: :approval help", handled=False)


# ── :branch ──────────────────────────────────────────────────────────────────

def handle_branch_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "list"
    rest = args[1:]

    if sub in ("help", "?"):
        return CommandResult(
            state.with_updates(status_message="branch: list|select|reject"),
            "\n".join([
                ":branch list [<task_id>]",
                ":branch select <task_id> <branch_id> [--reason TEXT]",
                ":branch reject <task_id> <branch_id> [--reason TEXT]",
            ]),
        )

    if sub == "list":
        task_id = rest[0] if rest else None
        if not task_id:
            return CommandResult(state.with_updates(status_message="branch list: task_id required"), "branch list: task_id required", handled=False)
        ok, resp = _api(state, "GET", f"/api/tasks/{task_id}/branches")
        if not ok:
            msg = f"branch list failed: {resp.get('error', resp)}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        branches = resp.get("branches") or []
        if not branches:
            return CommandResult(state.with_updates(status_message="keine Branches"), "Keine Branch-Kandidaten.")
        lines = [f"Branches für Task {task_id}: {len(branches)}"]
        for b in branches:
            lines.append(
                f"  {str(b.get('branch_id', ''))[:12]}  [{b.get('status')}]  {b.get('label')}  ({b.get('branch_type')})"
            )
        return CommandResult(state.with_updates(status_message=f"{len(branches)} Branches"), "\n".join(lines))

    if sub == "select":
        if len(rest) < 2:
            return CommandResult(state.with_updates(status_message="branch select: task_id + branch_id required"), "branch select <task_id> <branch_id>", handled=False)
        task_id, branch_id = rest[0], rest[1]
        _, reason, _ = _parse_task_reason_args(rest[2:])
        ok, resp = _send_command(state, task_id, "select_branch", {
            "branch_id": branch_id,
            "reason": reason or "",
        })
        if not ok or (resp.get("command", {}) or {}).get("status") not in ("applied", "accepted"):
            msg = _extract_error(resp)
            return CommandResult(state.with_updates(status_message=f"branch select failed: {msg}"), f"branch select {branch_id[:12]}: {msg}", handled=False)
        msg = f"branch {branch_id[:12]} selected"
        return CommandResult(state.with_updates(status_message=msg), msg)

    if sub == "reject":
        if len(rest) < 2:
            return CommandResult(state.with_updates(status_message="branch reject: task_id + branch_id required"), "branch reject <task_id> <branch_id>", handled=False)
        task_id, branch_id = rest[0], rest[1]
        _, reason, _ = _parse_task_reason_args(rest[2:])
        ok, resp = _api(state, "POST", f"/api/tasks/{task_id}/commands", {
            "type": "select_branch",
            "payload": {"branch_id": branch_id, "action": "reject", "reason": reason or ""},
        })
        msg = f"branch {branch_id[:12]}: rejected (via select_branch with rejected status)"
        return CommandResult(state.with_updates(status_message=msg), msg)

    return CommandResult(state.with_updates(status_message=f"branch: unbekannt '{sub}'"), f"branch {sub}: unbekannt. Hilfe: :branch help", handled=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_instruction_args(parts: list[str]) -> tuple[list[str], str]:
    """Split '--mode MODE text words' into (text_parts, mode)."""
    mode = "next_iteration_instruction"
    text_parts: list[str] = []
    skip_next = False
    for i, p in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if p == "--mode" and i + 1 < len(parts):
            mode = parts[i + 1]
            skip_next = True
        else:
            text_parts.append(p)
    return text_parts, mode


def _parse_task_reason_args(parts: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Parse --task ID --reason TEXT from arg list."""
    task_id: str | None = None
    reason_parts: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == "--task" and i + 1 < len(parts):
            task_id = parts[i + 1]
            i += 2
        elif parts[i] == "--reason" and i + 1 < len(parts):
            reason_parts.extend(parts[i + 1:])
            break
        else:
            remaining.append(parts[i])
            i += 1
    reason = " ".join(reason_parts).strip() or None
    return task_id, reason, remaining


def _cmd_result(state: OperatorState, label: str, task_id: str, ok: bool, resp: Any) -> CommandResult:
    cmd = (resp.get("command") or {}) if isinstance(resp, dict) else {}
    status = cmd.get("status", "?")
    if not ok or status == "failed":
        msg = f"{label} {task_id[:12]}: {_extract_error(resp)}"
        return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
    if status == "rejected_by_policy":
        msg = f"{label} {task_id[:12]}: rejected — {cmd.get('result', {}).get('error', 'policy')}"
        return CommandResult(state.with_updates(status_message=msg), msg)
    msg = f"{label} {task_id[:12]}: {status}"
    return CommandResult(state.with_updates(status_message=msg), msg)


def _extract_error(resp: Any) -> str:
    if not isinstance(resp, dict):
        return str(resp)[:100]
    cmd = resp.get("command") or {}
    result = cmd.get("result") or {}
    return str(result.get("error") or resp.get("error") or resp.get("message") or "unknown")[:120]
