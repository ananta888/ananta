"""AiProposalGuardrails — anti-hallucination guard for AI-generated heuristic proposals.

Checks:
  1. provenance.created_by is present and non-empty
  2. description is present and non-empty (no blank/boilerplate)
  3. status is not "active" (AI cannot propose active heuristics)
  4. python_strategy module is in the known allowlist prefix
  5. No invented capability names (only known caps allowed)
  6. heuristic_id follows snake_case naming convention
  7. No inline_code field anywhere in the dict (forbidden execution path)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_KNOWN_CAPABILITIES = frozenset({
    "read_local_context",
    "write_local_notes",
    "send_to_chat",
    "read_source_refs",
    "read_heuristic_index",
    "read_scope",
    "read_goal_state",
    "read_todo_state",
    "read_artifact_ref",
    "read_ui_state",
    "ui_motion",
    "file_write",
    "network_access",
    "secret_access",
})

_ALLOWED_MODULE_PREFIX = "agent.heuristics.strategies."
_FORBIDDEN_STATUSES_FROM_AI = {"active"}
_BOILERPLATE_DESCRIPTIONS = {
    "", "description", "todo", "tbd", "n/a", "none", "placeholder",
}


@dataclass
class GuardrailResult:
    passed: bool
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "rejection_reasons": list(self.rejection_reasons),
            "warnings": list(self.warnings),
        }


class AiProposalGuardrails:
    """Validates AI-generated heuristic proposal dicts before they enter the review pipeline."""

    def check(self, proposal: dict[str, Any]) -> GuardrailResult:
        reasons: list[str] = []
        warnings: list[str] = []

        # 1 — provenance.created_by required
        provenance = proposal.get("provenance") or {}
        created_by = str(provenance.get("created_by") or "").strip()
        if not created_by:
            reasons.append("missing_provenance_created_by")

        # 2 — description required and non-boilerplate
        description = str(proposal.get("description") or "").strip().lower()
        if description in _BOILERPLATE_DESCRIPTIONS:
            reasons.append(f"boilerplate_or_missing_description:{description!r}")

        # 3 — AI cannot claim status=active
        status = str(proposal.get("status") or "").strip().lower()
        if status in _FORBIDDEN_STATUSES_FROM_AI:
            reasons.append(f"ai_cannot_set_status_active:found_status={status!r}")

        # 4 — heuristic_id must follow snake_case
        hid = str(proposal.get("heuristic_id") or "")
        if hid and not _SNAKE_CASE_RE.match(hid):
            reasons.append(f"heuristic_id_not_snake_case:{hid!r}")

        # 5 — no inline_code anywhere (recursive search)
        if self._contains_inline_code(proposal):
            reasons.append("inline_code_field_forbidden")

        # 6 — python_strategy module must use allowed prefix
        runtime = proposal.get("runtime") or {}
        if isinstance(runtime, dict) and runtime.get("mode") == "python_strategy":
            ps = runtime.get("python_strategy") or {}
            if isinstance(ps, dict):
                module = str(ps.get("module") or "").strip()
                if module and not module.startswith(_ALLOWED_MODULE_PREFIX):
                    reasons.append(
                        f"python_strategy_module_not_in_allowed_prefix:{module!r} "
                        f"(must start with {_ALLOWED_MODULE_PREFIX!r})"
                    )

        # 7 — only known capability names (warn on unknown, reject on clearly invented)
        caps = list(proposal.get("capabilities") or [])
        for cap in caps:
            cap_str = str(cap).strip()
            if cap_str not in _KNOWN_CAPABILITIES:
                # Warn for unknown caps; if they look suspicious, reject
                if any(c in cap_str for c in (" ", "\n", ";", "import", "exec", "eval")):
                    reasons.append(f"suspicious_capability_name:{cap_str!r}")
                else:
                    warnings.append(f"unknown_capability:{cap_str!r}")

        passed = len(reasons) == 0
        return GuardrailResult(passed=passed, rejection_reasons=reasons, warnings=warnings)

    def _contains_inline_code(self, obj: Any, _depth: int = 0) -> bool:
        if _depth > 8:
            return False
        if isinstance(obj, dict):
            if "inline_code" in obj:
                return True
            return any(self._contains_inline_code(v, _depth + 1) for v in obj.values())
        if isinstance(obj, list):
            return any(self._contains_inline_code(item, _depth + 1) for item in obj)
        return False
