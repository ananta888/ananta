"""OHA-004: ToolOutputCompactionService.

Regelbasierte Kompression für Tool- und Shell-Ausgaben, bevor sie in
output_parts, ResultMemory oder Planner-Kontext gelangen.

Kein LLM im Hot-Path.
Fehler, Tracebacks, Security-Signale werden immer bewahrt.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.common.utils.tool_output_compaction_utils import (
    apply_keep_first_last,
    build_omitted_summary,
    collapse_blank_lines,
    find_preserved_lines,
    original_ref,
)
from agent.services.tool_output_compaction_rule_loader import CompactionRuleSet, load_rules

logger = logging.getLogger(__name__)

_DEFAULT_MAX_INPUT_CHARS = 4000
_DEFAULT_MAX_OUTPUT_CHARS = 2000


@dataclass
class CompactionResult:
    compacted_text: str
    original_ref: str
    omitted_summary: str
    preserved_signals: list[str]
    compaction_ratio: float        # output_chars / input_chars; 1.0 = no compaction
    applied_rule_ids: list[str]
    input_chars: int
    output_chars: int


def _normalize_text(text: str | None) -> str:
    return str(text or "").rstrip()


class ToolOutputCompactionService:
    """Compresses tool and shell output using deterministic rule-based compaction."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        fail_open: bool = True,
        builtin_rules_enabled: bool = True,
        project_rules_path: str | None = None,
        max_input_chars_for_compaction: int = _DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
        always_preserve_signals: bool = True,
    ) -> None:
        self._enabled = enabled
        self._fail_open = fail_open
        self._max_input = max_input_chars_for_compaction
        self._max_output = max_output_chars
        self._always_preserve = always_preserve_signals
        try:
            self._rules: CompactionRuleSet = load_rules(
                project_rules_path=project_rules_path,
                builtin_rules_enabled=builtin_rules_enabled,
            )
        except Exception as exc:
            logger.warning("ToolOutputCompactionService: failed to load rules — %s", exc)
            self._rules = CompactionRuleSet([])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compact(
        self,
        *,
        tool_name: str,
        output: str,
        command: str | None = None,
        error: str | None = None,
        exit_code: int | None = None,
        task_kind: str | None = None,
        risk_context: dict | None = None,
    ) -> CompactionResult:
        """Compact tool/shell output. Always safe to call — never raises."""
        combined = _normalize_text(output)
        if error:
            err_text = _normalize_text(error)
            if err_text and err_text not in combined:
                combined = combined + "\n" + err_text if combined else err_text

        ref = original_ref(combined)
        input_chars = len(combined)

        if not self._enabled or input_chars <= self._max_input:
            return CompactionResult(
                compacted_text=combined,
                original_ref=ref,
                omitted_summary="",
                preserved_signals=[],
                compaction_ratio=1.0,
                applied_rule_ids=[],
                input_chars=input_chars,
                output_chars=len(combined),
            )

        try:
            return self._do_compact(
                tool_name=tool_name,
                combined=combined,
                ref=ref,
                input_chars=input_chars,
            )
        except Exception as exc:
            logger.warning("ToolOutputCompactionService: compaction failed for %r — %s", tool_name, exc)
            if self._fail_open:
                return CompactionResult(
                    compacted_text=combined,
                    original_ref=ref,
                    omitted_summary=f"[compaction error: {exc}]",
                    preserved_signals=[],
                    compaction_ratio=1.0,
                    applied_rule_ids=[],
                    input_chars=input_chars,
                    output_chars=len(combined),
                )
            raise

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _do_compact(
        self,
        *,
        tool_name: str,
        combined: str,
        ref: str,
        input_chars: int,
    ) -> CompactionResult:
        lines = combined.splitlines()
        applied: list[str] = []

        # Step 1: find preserved signal lines
        preserved_indices: set[int] = set()
        preserved_signals: list[str] = []
        if self._always_preserve and self._rules.preserve_patterns:
            patterns = [pat for pat, _ in self._rules.preserve_patterns]
            preserved_indices = find_preserved_lines(lines, patterns)
            preserved_signals = [lines[i] for i in sorted(preserved_indices)]
            if preserved_indices:
                applied += [rule_id for _, rule_id in self._rules.preserve_patterns]

        # Step 2: determine truncation parameters
        trunc_rule = self._rules.truncate_rule_for_tool(tool_name)
        if trunc_rule:
            head_lines: int = int(trunc_rule.get("head_lines") or 30)
            tail_lines: int = int(trunc_rule.get("tail_lines") or 50)
            rule_id: str = str(trunc_rule.get("id") or "generic_truncate")
            applied.append(rule_id)
        else:
            head_lines = 30
            tail_lines = 50
            rule_id = "generic_truncate_fallback"
            applied.append(rule_id)

        # Step 3: apply keep_first_last
        kept_lines, _ = apply_keep_first_last(
            lines,
            head_lines=head_lines,
            tail_lines=tail_lines,
            preserved_indices=preserved_indices,
        )

        # Step 4: dedup blank lines
        kept_lines = collapse_blank_lines(kept_lines)

        # Step 5: build omitted summary
        omitted = build_omitted_summary(
            total_lines=len(lines),
            head=head_lines,
            tail=tail_lines,
            preserved_count=len(preserved_indices),
            applied_rule_ids=list(dict.fromkeys(applied)),
        )

        # Step 6: assemble and enforce max_output_chars
        result_text = "\n".join(kept_lines)
        if omitted:
            result_text = result_text + "\n" + omitted

        if len(result_text) > self._max_output:
            result_text = result_text[: self._max_output]
            result_text += f"\n[truncated to {self._max_output} chars]"

        output_chars = len(result_text)
        ratio = output_chars / input_chars if input_chars > 0 else 1.0

        return CompactionResult(
            compacted_text=result_text,
            original_ref=ref,
            omitted_summary=omitted,
            preserved_signals=preserved_signals,
            compaction_ratio=round(ratio, 4),
            applied_rule_ids=list(dict.fromkeys(applied)),
            input_chars=input_chars,
            output_chars=output_chars,
        )


# ---------------------------------------------------------------------------
# Module-level singleton + factory (follows Ananta service conventions)
# ---------------------------------------------------------------------------

_tool_output_compaction_service: ToolOutputCompactionService | None = None


def _build_from_config(cfg: dict | None) -> ToolOutputCompactionService:
    c = cfg if isinstance(cfg, dict) else {}
    return ToolOutputCompactionService(
        enabled=bool(c.get("enabled", True)),
        fail_open=bool(c.get("fail_open", True)),
        builtin_rules_enabled=bool(c.get("builtin_rules_enabled", True)),
        project_rules_path=c.get("project_rules_path") or None,
        max_input_chars_for_compaction=int(c.get("max_input_chars_for_compaction") or _DEFAULT_MAX_INPUT_CHARS),
        max_output_chars=int(c.get("max_output_chars") or _DEFAULT_MAX_OUTPUT_CHARS),
        always_preserve_signals=bool(c.get("always_preserve_signals", True)),
    )


def get_tool_output_compaction_service(cfg: dict | None = None) -> ToolOutputCompactionService:
    global _tool_output_compaction_service
    if _tool_output_compaction_service is None:
        _tool_output_compaction_service = _build_from_config(cfg)
    return _tool_output_compaction_service
