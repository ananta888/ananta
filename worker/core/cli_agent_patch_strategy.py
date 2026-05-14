"""WSM-T003: codex-cli-like patch proposal strategy."""
from __future__ import annotations

import re

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, PatchProposalArtifact, ExecutableProposal


class CliAgentPatchStrategy(ProposeStrategy):
    """Produces patch proposals and, when safe, executable file_patch steps."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        raw = ""
        if isinstance(context.research_context, dict):
            raw = str(context.research_context.get("raw_output") or "")
        if not raw:
            raw = str(context.base_prompt or "")
        if "---" not in raw and "+++" not in raw and "@@" not in raw:
            return ProposeStrategyResult.declined(
                "cli_agent_patch_strategy",
                reason="no_patch_content_detected",
                reason_codes=["no_patch_content"],
            )

        parsed = self._extract_unified_diff_patches(raw)
        safe_patch_ops = [p for p in parsed if self._is_workspace_safe_patch_path(str(p.get("path") or ""))]

        # Preferred codex-cli-like path: safe patch -> explicit executable tool call.
        if safe_patch_ops:
            proposal = ExecutableProposal.from_tool_calls(
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="cli_agent_patch_strategy",
                tool_calls=[{"name": "file_patch", "args": op} for op in safe_patch_ops],
                required_tools=["file_patch"],
                reason="safe_patch_executable_generated",
                metadata={
                    "source_format": "unified_diff",
                    "patch_count": len(safe_patch_ops),
                    "workspace_safe": True,
                },
            )
            return ProposeStrategyResult.executable(
                "cli_agent_patch_strategy",
                proposal,
                reason="safe_patch_executable_generated",
                reason_codes=["workspace_safe_patch"],
                metadata={"source_format": "unified_diff", "patch_count": len(safe_patch_ops)},
            )

        artifact = PatchProposalArtifact(
            proposal_id=f"cli-patch-{context.task_id}",
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="cli_agent_patch_strategy",
            patches=parsed or [{"path": "auto", "content": raw[:8000]}],
            metadata={"source_format": "unified_diff"},
        )
        return ProposeStrategyResult(
            status="advisory",
            strategy_id="cli_agent_patch_strategy",
            proposal=artifact,
            reason="patch_proposal_extracted",
            reason_codes=["patch_only_requires_apply_approval"],
            metadata={"source_format": "unified_diff", "patch_count": len(parsed or [])},
        )

    @staticmethod
    def _is_workspace_safe_patch_path(path: str) -> bool:
        candidate = str(path or "").strip()
        if not candidate:
            return False
        if candidate.startswith("/") or candidate.startswith("~"):
            return False
        if ":" in candidate[:3]:
            return False
        if ".." in candidate.split("/"):
            return False
        return True

    @classmethod
    def _extract_unified_diff_patches(cls, raw: str) -> list[dict]:
        text = str(raw or "")
        lines = text.splitlines()
        patches: list[dict] = []
        current_path: str | None = None
        minus_lines: list[str] = []
        plus_lines: list[str] = []
        for line in lines:
            if line.startswith("+++ "):
                if current_path and minus_lines and plus_lines:
                    patches.append(
                        {
                            "path": current_path,
                            "search": "\n".join(minus_lines)[:8000],
                            "replace": "\n".join(plus_lines)[:8000],
                        }
                    )
                current_path = cls._normalize_patch_path(line[4:].strip())
                minus_lines = []
                plus_lines = []
                continue
            if current_path is None:
                continue
            if line.startswith("--- ") or line.startswith("@@ "):
                continue
            if line.startswith("-") and not line.startswith("---"):
                minus_lines.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                plus_lines.append(line[1:])
        if current_path and minus_lines and plus_lines:
            patches.append(
                {
                    "path": current_path,
                    "search": "\n".join(minus_lines)[:8000],
                    "replace": "\n".join(plus_lines)[:8000],
                }
            )
        return [p for p in patches if str(p.get("path") or "").strip() and str(p.get("search") or "").strip()]

    @staticmethod
    def _normalize_patch_path(path_token: str) -> str:
        token = str(path_token or "").strip()
        token = re.sub(r"^[ab]/", "", token)
        return token
