"""AWTCL-009/010/012: hub-controlled tool calling loop for ananta-worker.

Flow per iteration: the worker LLM answers with one JSON object
(``ananta_worker_tool_loop.v1``). ``tool_request`` goes through the tool
registry and policy gate; allowed tools are executed deterministically
by the hub and the ToolResult is embedded as evidence into the next LLM
round. ``final_answer`` ends the loop. Invalid model output falls back
to a plain text answer after ``max_invalid_outputs`` attempts — the
existing context batch loop stays the fallback when the feature flag is
off (AWTCL-011).

Contract: ``docs/contracts/ananta-worker-tool-loop.md``.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import time
import uuid
from typing import Any, Callable

from agent.cli_backends.helpers import _get_agent_config
from agent.cli_backends.context import default_context as _ctx

log = logging.getLogger(__name__)

TOOL_LOOP_SCHEMA = "ananta_worker_tool_loop.v1"

KIND_TOOL_REQUEST = "tool_request"
KIND_FINAL_ANSWER = "final_answer"
KIND_NEEDS_APPROVAL = "needs_approval"
KIND_CANNOT_CONTINUE = "cannot_continue_without_context"

_VALID_KINDS = {KIND_TOOL_REQUEST, KIND_FINAL_ANSWER, KIND_NEEDS_APPROVAL, KIND_CANNOT_CONTINUE}

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def get_tool_loop_config() -> dict[str, Any]:
    cfg = dict(_get_agent_config().get("ananta_worker_tool_loop") or {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "max_iterations": max(1, min(int(cfg.get("max_iterations") or 6), 32)),
        "max_tool_calls": max(1, min(int(cfg.get("max_tool_calls") or 12), 64)),
        "max_tool_result_chars": max(500, min(int(cfg.get("max_tool_result_chars") or 8000), 100000)),
        "max_invalid_outputs": max(1, min(int(cfg.get("max_invalid_outputs") or 2), 10)),
        "allowed_tools": [str(item or "").strip() for item in list(cfg.get("allowed_tools") or []) if str(item or "").strip()],
    }


def _extract_json_candidate(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    fenced = _FENCED_JSON_RE.search(raw)
    if fenced:
        return fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return None


def parse_worker_tool_output(text: str) -> dict[str, Any] | None:
    """AWTCL-009: parse one worker LLM answer into a tool-loop message.

    Accepts raw JSON and fenced JSON. Returns ``None`` for anything that
    is not a valid tool-loop message so callers can fall back to treating
    the output as a normal text answer.
    """
    candidate = _extract_json_candidate(text)
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _VALID_KINDS:
        return None
    if kind == KIND_TOOL_REQUEST:
        tool_name_val = str(payload.get("tool_name") or "").strip()
        if not tool_name_val:
            return None
        # UTCR-009: reject tool_name strings that contain whitespace
        if any(c.isspace() for c in tool_name_val):
            return None
        if not isinstance(payload.get("arguments", {}), dict):
            return None
    return payload


def _validate_tool_arguments(spec: Any, arguments: dict[str, Any]) -> list[str]:
    """UTCR-009: validate arguments dict against spec; return warning strings.

    Only checks for unknown keys — missing required keys are left to the
    executor so the LLM gets a meaningful error rather than a terse block.
    Returns an empty list when everything is fine.
    """
    warnings: list[str] = []
    if not isinstance(arguments, dict):
        warnings.append("arguments_not_a_dict")
        return warnings
    if spec is None:
        return warnings
    known = set((getattr(spec, "argument_schema", {}) or {}).get("properties", {}).keys())
    if not known:
        return warnings
    for key in arguments:
        if key not in known:
            warnings.append(f"unknown_argument:{key}")
    return warnings


def build_tool_loop_instructions(*, allowed_tools_description: str) -> str:
    """AWTCL-012: prompt contract for the tool loop."""
    return "\n".join(
        [
            "## Tool-Loop-Protokoll (ananta_worker_tool_loop.v1)",
            "",
            "Antworte mit GENAU EINEM JSON-Objekt (roh oder in einem ```json Fence).",
            "Erlaubte `kind`-Werte:",
            '- `tool_request`  — {"kind": "tool_request", "tool_name": "...", "reason": "...", "arguments": {...}, "risk_hint": "read|write|execution"}',
            '- `final_answer`  — {"kind": "final_answer", "answer": "...", "evidence_refs": ["tool_result:N", ...]}',
            '- `needs_approval` — wenn eine Aktion nur mit separatem Hub-Approval möglich ist.',
            '- `cannot_continue_without_context` — wenn deterministische Daten fehlen und kein Tool sie liefern kann.',
            "",
            "Regeln:",
            "- NICHT raten: Wenn deterministische Daten fehlen (Dateiinhalte, Suchtreffer, Testausgaben), fordere ein Tool an.",
            "- Behaupte KEINE Ausführung, die der Hub nicht per ToolResult bestätigt hat.",
            "- Tools werden ausschließlich vom Hub ausgeführt; du forderst sie nur an.",
            "- Beziehe dich in `final_answer` auf die gelieferten ToolResults (evidence_refs).",
            "",
            "Verfügbare Tools:",
            allowed_tools_description or "- (keine Tools freigegeben — antworte mit final_answer)",
        ]
    )


def _format_tool_result_block(result: dict[str, Any], *, max_chars: int) -> str:
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if len(serialized) > max_chars:
        serialized = serialized[: max_chars - 14] + "\n…[truncated]"
    return f"```json\n{serialized}\n```"


def build_tool_loop_prompt(
    *,
    original_prompt: str,
    instructions: str,
    tool_results: list[dict[str, Any]],
    iteration: int,
    max_iterations: int,
    max_tool_result_chars: int,
) -> str:
    parts = [
        str(original_prompt or "").rstrip(),
        "",
        "---",
        "",
        instructions,
        "",
        f"Iteration {iteration}/{max_iterations}.",
    ]
    if tool_results:
        parts += ["", "## Bisherige ToolResults (Evidence)"]
        for result in tool_results:
            parts.append(_format_tool_result_block(result, max_chars=max_tool_result_chars))
    return "\n".join(parts)


def register_pending_approval_request(
    *,
    task_id: str | None,
    tool_name: str,
    arguments: dict[str, Any] | None,
    risk_class: str = "unknown",
    reason: str | None = None,
) -> str | None:
    """ALWA-007: persist a pending ApprovalRequest when a loop stops on approval.

    Guarded by the approval_lifecycle feature flag; failures are non-fatal
    (worker contexts without DB simply skip registration — the outcome
    stays visible in the workspace report either way).
    """
    try:
        svc = _ctx.approval_request_service
        if not svc.get_lifecycle_config().get("enabled"):
            return None
        request = svc.create_pending_request(
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
            risk_class=risk_class,
            scope={"source": "ananta_worker_loop", "reason": str(reason or "")[:300]},
        )
        return request.id
    except Exception:
        log.debug("pending approval registration failed (non-fatal)", exc_info=True)
        return None


def run_ananta_worker_tool_loop(
    prompt: str,
    workdir: str | None,
    *,
    options: list,
    timeout: int,
    model: str | None,
    llm_runner: Callable[..., tuple[int, str, str]] | None = None,
    config: dict[str, Any] | None = None,
    mutation_mode: str = "read_only",
    task_id: str | None = None,
    # UTCR-008: optional metadata for tool-loop-report.json
    backend: str | None = None,
    provider: str | None = None,
) -> tuple[int, str, str]:
    """AWTCL-010: run the hub-controlled tool loop and return (rc, out, err)."""
    cfg = dict(config or get_tool_loop_config())
    if llm_runner is None:
        from agent.cli_backends.sgpt import run_sgpt_command

        llm_runner = run_sgpt_command

    registry = _ctx.ananta_tool_registry_service
    policy = _ctx.ananta_tool_policy_service
    from agent.services.tools import execute_ananta_tool
    instructions = build_tool_loop_instructions(
        allowed_tools_description=registry.describe_for_prompt(cfg.get("allowed_tools"))
    )

    session_id = uuid.uuid4().hex[:12]
    max_iterations = int(cfg.get("max_iterations") or 6)
    max_tool_calls = int(cfg.get("max_tool_calls") or 12)
    max_result_chars = int(cfg.get("max_tool_result_chars") or 8000)
    max_invalid = int(cfg.get("max_invalid_outputs") or 2)

    tool_results: list[dict[str, Any]] = []
    report_iterations: list[dict[str, Any]] = []
    tool_call_count = 0
    invalid_count = 0
    last_rc, last_out, last_err = 0, "", ""

    def _audit(action: str, *, tool_name: str, decision: str, risk: str, status: str | None = None, detail: str | None = None) -> None:
        try:
            from agent.common.audit import audit_worker_tool_event

            audit_worker_tool_event(
                action,
                tool_name=tool_name,
                policy_decision=decision,
                risk_class=risk,
                task_id=task_id,
                session_id=session_id,
                status=status,
                detail=detail,
            )
        except Exception:
            log.debug("tool loop audit failed (non-fatal)", exc_info=True)

    def _write_report(outcome: str) -> None:
        if not workdir:
            return
        try:
            report_path = pathlib.Path(workdir) / ".ananta" / "tool-loop-report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            # UTCR-008: extended report with mode / backend / model metadata
            effective_allowed = [
                str(item or "").strip()
                for item in (cfg.get("allowed_tools") or [])
                if str(item or "").strip()
            ]
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "ananta_worker_tool_loop_report.v1",
                        "session_id": session_id,
                        "task_id": task_id,
                        "outcome": outcome,
                        "tool_call_count": tool_call_count,
                        "invalid_output_count": invalid_count,
                        "created_at": time.time(),
                        "iterations": report_iterations,
                        # UTCR-008 additions
                        "tool_calling_mode": "prompt_json_protocol",
                        "schema_format": "prompt_json_description",
                        "effective_allowed_tools": effective_allowed,
                        "backend": str(backend or ""),
                        "provider": str(provider or ""),
                        "model": str(model or ""),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    for iteration in range(1, max_iterations + 1):
        iter_prompt = build_tool_loop_prompt(
            original_prompt=prompt,
            instructions=instructions,
            tool_results=tool_results,
            iteration=iteration,
            max_iterations=max_iterations,
            max_tool_result_chars=max_result_chars,
        )
        rc, out, err = llm_runner(prompt=iter_prompt, options=list(options or []), timeout=timeout, model=model, workdir=workdir)
        last_rc, last_out, last_err = rc, out, err
        if rc != 0 and not out:
            _write_report("llm_failed")
            return rc, out, err

        message = parse_worker_tool_output(out)
        if message is None:
            invalid_count += 1
            report_iterations.append({"iteration": iteration, "kind": "invalid_output"})
            if invalid_count >= max_invalid:
                # Controlled fallback: treat the raw output as a plain text answer.
                _write_report("invalid_output_fallback")
                return rc, out, err
            tool_results.append(
                {
                    "schema": "ananta_tool_result.v1",
                    "tool_call_id": f"protocol:{iteration}",
                    "tool_name": "protocol",
                    "status": "invalid_output",
                    "risk_class": "low",
                    "evidence": [],
                    "warnings": ["previous_answer_was_not_valid_tool_loop_json"],
                }
            )
            continue

        kind = str(message.get("kind"))
        if kind == KIND_FINAL_ANSWER:
            report_iterations.append({"iteration": iteration, "kind": kind})
            _write_report("final_answer")
            return 0, str(message.get("answer") or out), err
        if kind in {KIND_NEEDS_APPROVAL, KIND_CANNOT_CONTINUE}:
            report_iterations.append({"iteration": iteration, "kind": kind, "reason": str(message.get("reason") or "")})
            _write_report(kind)
            summary = {
                "kind": kind,
                "reason": str(message.get("reason") or ""),
                "tool_results_so_far": len(tool_results),
            }
            if kind == KIND_NEEDS_APPROVAL:
                request_id = register_pending_approval_request(
                    task_id=task_id,
                    tool_name=str(message.get("tool_name") or "worker.needs_approval"),
                    arguments=dict(message.get("arguments") or {}),
                    reason=str(message.get("reason") or ""),
                )
                if request_id:
                    summary["approval_request_id"] = request_id
            return 0, json.dumps(summary, ensure_ascii=False), err

        # kind == tool_request
        tool_name = str(message.get("tool_name") or "").strip()
        arguments = dict(message.get("arguments") or {})
        tool_call_count += 1
        tool_call_id = f"tool_result:{tool_call_count}"
        decision = policy.evaluate(
            tool_name=tool_name,
            arguments=arguments,
            allowed_tools=cfg.get("allowed_tools"),
            mutation_mode=mutation_mode,
            task_id=task_id,
        )
        report_iterations.append(
            {
                "iteration": iteration,
                "kind": kind,
                "tool_name": tool_name,
                "policy_decision": decision.decision,
                "policy_reason": decision.reason,
            }
        )
        from agent.common.audit import (
            AUDIT_WORKER_TOOL_APPROVAL_REQUIRED,
            AUDIT_WORKER_TOOL_BLOCKED,
            AUDIT_WORKER_TOOL_COMPLETED,
            AUDIT_WORKER_TOOL_REQUESTED,
        )

        # UTCR-009: explicit registry check — defense-in-depth on top of policy gate.
        # Uses the policy gate's decision when available (it already handles unknown tools);
        # only falls back to a synthetic block when the policy gate somehow missed it.
        if not decision.allowed and decision.reason.startswith("unknown_tool:"):
            # Policy gate already caught it — fall through to the normal blocked path below.
            pass
        elif registry.get_tool(tool_name) is None and decision.allowed:
            # Paranoid check: tool was somehow allowed by policy but not in registry.
            from agent.services.tools._evidence import build_tool_result as _btr

            unknown_result = _btr(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="policy_blocked",
                risk_class="unknown",
                error=f"unknown_tool:{tool_name}",
                policy_decision={"decision": "policy_blocked", "reason": f"unknown_tool:{tool_name}", "rule_id": "registry_check", "tool_name": tool_name},
            )
            tool_results.append(unknown_result)
            continue

        _audit(AUDIT_WORKER_TOOL_REQUESTED, tool_name=tool_name, decision=decision.decision, risk=decision.risk_class)
        if not decision.allowed:
            blocked_action = (
                AUDIT_WORKER_TOOL_APPROVAL_REQUIRED
                if decision.decision == "approval_required"
                else AUDIT_WORKER_TOOL_BLOCKED
            )
            _audit(blocked_action, tool_name=tool_name, decision=decision.decision, risk=decision.risk_class, detail=decision.reason)
            from agent.services.tools._evidence import build_tool_result

            blocked_result = build_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status=decision.decision,
                risk_class=decision.risk_class,
                error=decision.reason,
                policy_decision=decision.as_dict(),
            )
            if decision.decision == "approval_required":
                request_id = register_pending_approval_request(
                    task_id=task_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    risk_class=decision.risk_class,
                    reason=decision.reason,
                )
                if request_id:
                    blocked_result["approval_request_id"] = request_id
            tool_results.append(blocked_result)
        else:
            # UTCR-009: validate arguments and attach warnings to result
            arg_warnings = _validate_tool_arguments(registry.get_tool(tool_name), arguments)
            result = execute_ananta_tool(
                tool_name=tool_name,
                arguments=arguments,
                workspace_dir=str(workdir or "."),
                tool_call_id=tool_call_id,
                config=cfg,
            )
            result["policy_decision"] = decision.as_dict()
            if arg_warnings:
                existing = list(result.get("warnings") or [])
                result["warnings"] = sorted(set(existing) | set(arg_warnings))
            _audit(
                AUDIT_WORKER_TOOL_COMPLETED,
                tool_name=tool_name,
                decision=decision.decision,
                risk=decision.risk_class,
                status=str(result.get("status") or ""),
            )
            tool_results.append(result)

        if tool_call_count >= max_tool_calls:
            _write_report("max_tool_calls_reached")
            summary = {
                "kind": "loop_aborted",
                "reason": "max_tool_calls_reached",
                "tool_results": tool_results,
            }
            return 0, json.dumps(summary, ensure_ascii=False), last_err

    _write_report("max_iterations_reached")
    summary = {
        "kind": "loop_aborted",
        "reason": "max_iterations_reached",
        "last_output": last_out[:2000],
        "tool_results_so_far": len(tool_results),
    }
    return 0, json.dumps(summary, ensure_ascii=False), last_err
