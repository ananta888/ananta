"""SCG-001/SCG-002: ShellCommandPolicy datamodel and ShellCommandAnalyzer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.command_chain_parser import CommandChainParser, CommandChainSegment

_DEFAULT_ALLOW_OPS: list[str] = [";", "&&", "||"]
_DEFAULT_DENY_OPS: list[str] = ["|", "`", "$(", "${", "<<"]
_DEFAULT_INLINE: dict[str, bool] = {"python -c": True, "node -e": True}


@dataclass
class ShellCommandPolicy:
    """Configurable policy for shell command chain validation."""

    enabled: bool = True
    allow_chain_operators: list[str] = field(default_factory=lambda: list(_DEFAULT_ALLOW_OPS))
    deny_operators: list[str] = field(default_factory=lambda: list(_DEFAULT_DENY_OPS))
    validate_segments_individually: bool = True
    allow_quoted_operators: bool = True
    allow_inline_language_code: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_INLINE))
    allow_complex_shell_mode: bool = False

    @classmethod
    def from_config(cls, cfg: dict | None) -> "ShellCommandPolicy":
        raw = dict(cfg or {})
        if not raw:
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", True)),
            allow_chain_operators=list(raw.get("allow_chain_operators", _DEFAULT_ALLOW_OPS)),
            deny_operators=list(raw.get("deny_operators", _DEFAULT_DENY_OPS)),
            validate_segments_individually=bool(raw.get("validate_segments_individually", True)),
            allow_quoted_operators=bool(raw.get("allow_quoted_operators", True)),
            allow_inline_language_code=dict(raw.get("allow_inline_language_code", _DEFAULT_INLINE)),
            allow_complex_shell_mode=bool(raw.get("allow_complex_shell_mode", False)),
        )

    @classmethod
    def from_agent_cfg(cls, agent_cfg: dict | None) -> "ShellCommandPolicy":
        cfg = (agent_cfg or {}).get("shell_command_policy")
        return cls.from_config(cfg if isinstance(cfg, dict) else None)


@dataclass(frozen=True)
class CommandChainAnalysisResult:
    """Result of ShellCommandAnalyzer.analyze()."""

    original_command: str
    allowed: bool
    denied_reason: str | None
    unsupported_operators: list[str]
    segments: list[CommandChainSegment]
    chain_operator_count: int
    contains_chain: bool
    contains_quoted_operators: bool
    policy_snapshot: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_command": self.original_command[:200],
            "allowed": self.allowed,
            "denied_reason": self.denied_reason,
            "unsupported_operators": list(self.unsupported_operators),
            "segment_count": len(self.segments),
            "chain_operator_count": self.chain_operator_count,
            "contains_chain": self.contains_chain,
            "contains_quoted_operators": self.contains_quoted_operators,
        }


class ShellCommandAnalyzer:
    """Apply ShellCommandPolicy to a command string using CommandChainParser.

    Deterministic and side-effect free — safe to run outside Flask app context.
    """

    def analyze(self, command: str | None, agent_cfg: dict | None = None) -> CommandChainAnalysisResult:
        policy = ShellCommandPolicy.from_agent_cfg(agent_cfg)
        return self._run(command, policy)

    def analyze_with_policy(self, command: str | None, policy: ShellCommandPolicy) -> CommandChainAnalysisResult:
        return self._run(command, policy)

    def _run(self, command: str | None, policy: ShellCommandPolicy) -> CommandChainAnalysisResult:
        text = str(command or "").strip()
        snapshot: dict[str, Any] = {
            "allow_chain_operators": list(policy.allow_chain_operators),
            "deny_operators": list(policy.deny_operators),
            "validate_segments_individually": policy.validate_segments_individually,
            "allow_quoted_operators": policy.allow_quoted_operators,
        }

        if not text:
            return CommandChainAnalysisResult(
                original_command=text,
                allowed=True,
                denied_reason=None,
                unsupported_operators=[],
                segments=[],
                chain_operator_count=0,
                contains_chain=False,
                contains_quoted_operators=False,
                policy_snapshot=snapshot,
            )

        plan = CommandChainParser().parse(text)

        # Operators that are always denied (pipes, substitutions, etc.)
        always_denied = [op for op in (plan.unsupported_operators or []) if op in policy.deny_operators]
        if always_denied:
            return CommandChainAnalysisResult(
                original_command=text,
                allowed=False,
                denied_reason="unsupported_operator",
                unsupported_operators=always_denied,
                segments=[],
                chain_operator_count=0,
                contains_chain=False,
                contains_quoted_operators=False,
                policy_snapshot=snapshot,
            )

        if not plan.allowed:
            return CommandChainAnalysisResult(
                original_command=text,
                allowed=False,
                denied_reason=plan.denied_reason,
                unsupported_operators=list(plan.unsupported_operators or []),
                segments=[],
                chain_operator_count=0,
                contains_chain=False,
                contains_quoted_operators=False,
                policy_snapshot=snapshot,
            )

        # Verify that every chain operator used is in the allow-list
        used_ops = {seg.operator_before for seg in plan.segments if seg.operator_before}
        disallowed = [op for op in sorted(used_ops) if op not in policy.allow_chain_operators]
        if disallowed:
            return CommandChainAnalysisResult(
                original_command=text,
                allowed=False,
                denied_reason="chain_operator_not_allowed",
                unsupported_operators=disallowed,
                segments=[],
                chain_operator_count=len(used_ops),
                contains_chain=len(plan.segments) > 1,
                contains_quoted_operators=False,
                policy_snapshot=snapshot,
            )

        contains_chain = len(plan.segments) > 1
        chain_op_count = len(used_ops)
        contains_quoted = _detect_quoted_operators(text, [seg.raw for seg in plan.segments])

        return CommandChainAnalysisResult(
            original_command=text,
            allowed=True,
            denied_reason=None,
            unsupported_operators=[],
            segments=list(plan.segments),
            chain_operator_count=chain_op_count,
            contains_chain=contains_chain,
            contains_quoted_operators=contains_quoted,
            policy_snapshot=snapshot,
        )


def _detect_quoted_operators(text: str, segment_raws: list[str]) -> bool:
    """True when a chain operator appears inside a segment (was inside quotes)."""
    for op in (";", "&&", "||"):
        if op not in text:
            continue
        for raw in segment_raws:
            if op in raw:
                return True
    return False
