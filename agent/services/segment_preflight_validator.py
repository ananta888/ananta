"""SCG-005: SegmentPreflightValidator — per-segment upfront policy check."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.command_chain_parser import CommandChainSegment
from agent.services.command_to_tool_mapper import CommandToToolMapper
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.task_execution_policy_service import resolve_task_scope_allowed_tools, validate_task_scoped_tool_calls


@dataclass(frozen=True)
class SegmentValidationRow:
    segment_index: int
    operator_before: str | None
    command_preview: str
    allowed: bool
    reason_codes: list[str]
    mapped_tool: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "operator_before": self.operator_before,
            "command_preview": self.command_preview,
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "mapped_tool": self.mapped_tool,
        }


@dataclass(frozen=True)
class SegmentPreflightResult:
    allowed: bool
    validations: list[SegmentValidationRow]
    denied_segment_index: int | None
    reason_codes: list[str]

    def as_validation_meta(self) -> list[dict[str, Any]]:
        return [row.as_dict() for row in self.validations]


class SegmentPreflightValidator:
    """Validates every segment of a parsed command chain before any execution."""

    def validate_segments(
        self,
        segments: list[CommandChainSegment],
        task: dict | None,
        agent_cfg: dict | None,
        known_tools: list[str] | None = None,
    ) -> SegmentPreflightResult:
        effective_task = task or {}
        cfg = dict(agent_cfg or {})
        _known_tools: list[str] = list(known_tools or [])
        known_tool_set = set(_known_tools)

        validations: list[SegmentValidationRow] = []
        for seg in segments:
            row = self._validate_one(seg, effective_task, cfg, known_tool_set, _known_tools)
            validations.append(row)
            if not row.allowed:
                return SegmentPreflightResult(
                    allowed=False,
                    validations=validations,
                    denied_segment_index=seg.index,
                    reason_codes=list(row.reason_codes),
                )

        return SegmentPreflightResult(
            allowed=True,
            validations=validations,
            denied_segment_index=None,
            reason_codes=[],
        )

    def _validate_one(
        self,
        seg: CommandChainSegment,
        effective_task: dict,
        cfg: dict,
        known_tool_set: set[str],
        known_tools: list[str],
    ) -> SegmentValidationRow:
        seg_cmd = seg.raw
        mapped = CommandToToolMapper().map(seg_cmd)
        mapped_is_known = bool(mapped.mapped_tool and mapped.mapped_tool in known_tool_set)
        mapped_tool_calls = [{"name": mapped.mapped_tool, "args": mapped.args}] if mapped_is_known else None

        seg_approval = get_approval_policy_service().evaluate(
            command=None if mapped_is_known else seg_cmd,
            tool_calls=mapped_tool_calls,
            task=effective_task,
            agent_cfg=cfg,
        ).as_dict()
        seg_risk = evaluate_execution_risk(
            command=None if mapped_is_known else seg_cmd,
            tool_calls=mapped_tool_calls,
            task=effective_task,
            agent_cfg=cfg,
        )

        allowed = True
        reason_codes: list[str] = []
        if seg_approval.get("classification") == "blocked" and seg_approval.get("enforced"):
            allowed = False
            reason_codes.append(str(seg_approval.get("reason_code") or "approval_blocked"))
        if not seg_risk.allowed:
            allowed = False
            reason_codes.extend([str(item) for item in (seg_risk.reasons or [])] or ["risk_policy_blocked"])
        if mapped_is_known:
            blocked, reasons = validate_task_scoped_tool_calls(
                mapped_tool_calls,
                allowed_tools=resolve_task_scope_allowed_tools(effective_task),
                known_tools=known_tools,
            )
            if blocked:
                allowed = False
                reason_codes.extend(list(reasons.values()))

        return SegmentValidationRow(
            segment_index=seg.index,
            operator_before=seg.operator_before,
            command_preview=seg_cmd[:200],
            allowed=allowed,
            reason_codes=list(dict.fromkeys(reason_codes)),
            mapped_tool=mapped.mapped_tool if mapped_is_known else None,
        )
