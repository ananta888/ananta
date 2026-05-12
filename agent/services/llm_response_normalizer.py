"""LLMResponseNormalizer — FA-T010 / AFR-T005: flexible LLM output normalization."""
from __future__ import annotations

import re
import json
from typing import List, Dict, Any, Optional
from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    AdvisoryProposalArtifact,
    PatchProposalArtifact,
    FileProposalArtifact,
    PlannerProposalArtifact,
    STATUS_ADVISORY,
    STATUS_EXECUTABLE,
)

# T005: reject absolute paths and traversal in LLM-proposed filenames
_UNSAFE_PATH_RE = re.compile(r"(?:^|/)\.\./|^/|^[A-Za-z]:[/\\]")


def _is_safe_path(path: str) -> bool:
    return bool(path) and not _UNSAFE_PATH_RE.search(path)


class LLMResponseNormalizer:
    MAX_TEXT_LENGTH = 5000

    def __init__(self):
        self._tool_calls_re = re.compile(r'"tool_calls"\s*:\s*\[([^\]]+)\]', re.DOTALL)
        self._fenced_json_re = re.compile(r'```(?:json|JSON)?\s*(\{.*?\})\s*```', re.DOTALL | re.IGNORECASE)
        self._fenced_shell_re = re.compile(r'```(?:bash|sh|shell)\s*(.*?)\s*```', re.DOTALL | re.IGNORECASE | re.MULTILINE)
        self._diff_re = re.compile(r'^(?:---|\+\+ +|\@@)', re.MULTILINE)
        self._file_block_re = re.compile(r'```(\w+\.?\w*)\s*(.*?)\s*```', re.DOTALL | re.IGNORECASE)

    def normalize(
        self,
        raw_text: str,
        context: ProposeContext,
        *,
        allow_shell_execution: bool = False,
    ) -> ProposeStrategyResult:
        trimmed = raw_text.strip()[:self.MAX_TEXT_LENGTH]

        result = self._try_tool_calls(trimmed, context)
        if result:
            return result

        result = self._try_fenced_json(trimmed, context)
        if result:
            return result

        result = self._try_fenced_shell(trimmed, context, allow_shell_execution=allow_shell_execution)
        if result:
            return result

        result = self._try_diff(trimmed, context)
        if result:
            return result

        result = self._try_file_blocks(trimmed, context)
        if result:
            return result

        result = self._try_planner(trimmed, context)
        if result:
            return result

        return ProposeStrategyResult.advisory(
            "llm_response_normalizer",
            advisory_text=trimmed,
            reason="free_text_normalized_to_advisory",
            reason_codes=["source_format:prose"],
            metadata={"source_format": "natural_language", "confidence": 0.2}
        )

    def _try_tool_calls(self, text: str, context: ProposeContext) -> Optional[ProposeStrategyResult]:
        try:
            parsed = json.loads(text)
            tool_calls = parsed.get("tool_calls", [])
            if tool_calls and isinstance(tool_calls, list):
                valid_tcs = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("name")]
                if valid_tcs:
                    proposal = ExecutableProposal(
                        proposal_id=f"norm-tool-{context.task_id}",
                        goal_id=context.goal_id,
                        task_id=context.task_id,
                        strategy_id="llm_response_normalizer",
                        tool_calls=valid_tcs,
                    )
                    return ProposeStrategyResult.executable(
                        "llm_response_normalizer",
                        proposal,
                        reason_codes=["source_format:openai_tool_calls"],
                        metadata={"confidence": 1.0, "source_format": "openai_tool_calls"},
                    )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        return None

    def _try_fenced_json(self, text: str, context: ProposeContext) -> Optional[ProposeStrategyResult]:
        match = self._fenced_json_re.search(text)
        if match:
            try:
                parsed = json.loads(match.group(1))
                command = parsed.get("command")
                tool_calls = parsed.get("tool_calls", [])
                valid_tcs = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("name")]
                if command or valid_tcs:
                    proposal = ExecutableProposal(
                        proposal_id=f"norm-fjson-{context.task_id}",
                        goal_id=context.goal_id,
                        task_id=context.task_id,
                        strategy_id="llm_response_normalizer",
                        command=command or None,
                        tool_calls=valid_tcs,
                    )
                    return ProposeStrategyResult.executable(
                        "llm_response_normalizer",
                        proposal,
                        metadata={"confidence": 0.95, "source_format": "fenced_json"}
                    )
            except json.JSONDecodeError:
                pass
        return None

    def _try_fenced_shell(
        self,
        text: str,
        context: ProposeContext,
        *,
        allow_shell_execution: bool = False,
    ) -> Optional[ProposeStrategyResult]:
        match = self._fenced_shell_re.search(text)
        if match:
            command = match.group(1).strip()
            if command:
                if not allow_shell_execution:
                    # Shell execution not allowed by policy → advisory
                    return ProposeStrategyResult.advisory(
                        "llm_response_normalizer",
                        advisory_text=command,
                        reason="shell_execution_not_allowed_by_policy",
                        reason_codes=["source_format:shell_block", "shell_execution_policy_denied"],
                        metadata={"source_format": "shell_block", "confidence": 0.7},
                    )
                proposal = ExecutableProposal.from_command(
                    goal_id=context.goal_id,
                    task_id=context.task_id,
                    strategy_id="llm_response_normalizer",
                    command=command,
                )
                return ProposeStrategyResult.executable(
                    "llm_response_normalizer",
                    proposal,
                    metadata={"confidence": 0.8, "source_format": "fenced_shell"}
                )
        return None

    def _try_diff(self, text: str, context: ProposeContext) -> Optional[ProposeStrategyResult]:
        if self._diff_re.search(text):
            patch_content = text[:2000]
            proposal = PatchProposalArtifact(
                proposal_id=f"norm-diff-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="llm_response_normalizer",
                patches=[{"path": "auto", "content": patch_content}],
            )
            return ProposeStrategyResult(
                status=STATUS_ADVISORY,
                strategy_id="llm_response_normalizer",
                proposal=proposal,
                reason="patch_proposal_extracted",
                metadata={"confidence": 0.85, "source_format": "unified_diff"}
            )
        return None

    def _try_file_blocks(self, text: str, context: ProposeContext) -> Optional[ProposeStrategyResult]:
        matches = self._file_block_re.finditer(text)
        files = []
        for m in matches:
            filename = m.group(1)
            content = m.group(2).strip()
            # Only treat as file block when it looks like a filename (has extension)
            # and path is safe (no traversal/absolute)
            if filename and "." in filename and content and _is_safe_path(filename):
                files.append({"path": filename, "content": content})
        if files:
            proposal = FileProposalArtifact(
                proposal_id=f"norm-file-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="llm_response_normalizer",
                files=files,
            )
            return ProposeStrategyResult(
                status=STATUS_ADVISORY,
                strategy_id="llm_response_normalizer",
                proposal=proposal,
                reason="file_proposal_extracted",
                metadata={"confidence": 0.9, "source_format": "file_blocks"}
            )
        return None

    def _try_planner(self, text: str, context: ProposeContext) -> Optional[ProposeStrategyResult]:
        if "sub-task" in text.lower() or "tasks:" in text.lower():
            sub_tasks = [{"task_id": "sub1", "title": "Subtask", "description": text[:200], "kind": "coding"}]
            proposal = PlannerProposalArtifact(
                proposal_id=f"norm-plan-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="llm_response_normalizer",
                sub_tasks=sub_tasks,
            )
            return ProposeStrategyResult(
                status=STATUS_ADVISORY,
                strategy_id="llm_response_normalizer",
                proposal=proposal,
                reason="planner_proposal_extracted",
                metadata={"confidence": 0.6, "source_format": "planner_text"}
            )
        return None
