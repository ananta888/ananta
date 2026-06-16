"""RTIPM-002: Path-based AI mode policy service.

Config key: ``path_ai_modes`` (list of glob rules).

Priority: exact path match > deeper glob > broader glob > default (no rule).
A ``blocked_ai_modes`` entry **always** wins over ``allowed_ai_modes`` for the
same path, unless a higher-priority rule explicitly allows it (handled by
first-match semantics after sorting by specificity).

Default (no config / no matching rule): all modes are allowed — existing
behavior is completely unchanged.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Mode constants (mirrors RTIPM target_modes) ───────────────────────────────
AI_MODE_FULL_LLM = "full_llm"
AI_MODE_DIRECT_LLM = "direct_llm"
AI_MODE_EMBEDDING_ONLY = "embedding_only"
AI_MODE_CODECOMPASS_ONLY = "codecompass_only"
AI_MODE_RESTRICTED_TRANSFORMER = "restricted_transformer_inference"
AI_MODE_DETERMINISTIC_ONLY = "deterministic_only"

# Extended blocked-mode aliases (used in config)
AI_MODE_EXTERNAL_CLOUD = "external_cloud_llm"
AI_MODE_CHAT_GENERATION = "chat_generation"
AI_MODE_CODE_GENERATION = "code_generation"

ALL_AI_MODES = frozenset({
    AI_MODE_FULL_LLM, AI_MODE_DIRECT_LLM, AI_MODE_EMBEDDING_ONLY,
    AI_MODE_CODECOMPASS_ONLY, AI_MODE_RESTRICTED_TRANSFORMER,
    AI_MODE_DETERMINISTIC_ONLY, AI_MODE_EXTERNAL_CLOUD,
    AI_MODE_CHAT_GENERATION, AI_MODE_CODE_GENERATION,
})


# ── Per-rule config ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PathAiModeRule:
    path_glob: str
    allowed_ai_modes: frozenset[str] = field(default_factory=frozenset)
    blocked_ai_modes: frozenset[str] = field(default_factory=frozenset)
    allowed_model_engines: frozenset[str] = field(default_factory=frozenset)
    allow_hidden_states: bool = True
    allow_logits: bool = True
    allow_attention: bool = True
    allow_free_text_generation: bool = True
    allow_tool_decision_from_model_text: bool = True
    allow_code_generation: bool = True
    require_controlled_write_policy: bool = False
    max_input_chars: int = 0          # 0 = no limit
    max_batch_size: int = 0           # 0 = no limit
    llm_scope: str = ""               # e.g. "local_only", "external_allowed"
    priority: int = 0                 # higher = evaluated first

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "PathAiModeRule":
        def _fs(key: str) -> frozenset[str]:
            return frozenset(str(m) for m in (raw.get(key) or []))

        def _b(key: str, default: bool) -> bool:
            v = raw.get(key)
            if v is None:
                return default
            return bool(v)

        def _i(key: str, default: int) -> int:
            try:
                return max(0, int(raw.get(key) or default))
            except (TypeError, ValueError):
                return default

        glob = str(raw.get("path_glob") or "*")
        # Heuristic priority: longer / more specific globs rank higher
        auto_priority = len(glob) - glob.count("*") * 5
        priority = _i("priority", auto_priority)

        return cls(
            path_glob=glob,
            allowed_ai_modes=_fs("allowed_ai_modes"),
            blocked_ai_modes=_fs("blocked_ai_modes"),
            allowed_model_engines=_fs("allowed_model_engines"),
            allow_hidden_states=_b("allow_hidden_states", True),
            allow_logits=_b("allow_logits", True),
            allow_attention=_b("allow_attention", True),
            allow_free_text_generation=_b("allow_free_text_generation", True),
            allow_tool_decision_from_model_text=_b("allow_tool_decision_from_model_text", True),
            allow_code_generation=_b("allow_code_generation", True),
            require_controlled_write_policy=_b("require_controlled_write_policy", False),
            max_input_chars=_i("max_input_chars", 0),
            max_batch_size=_i("max_batch_size", 0),
            llm_scope=str(raw.get("llm_scope") or ""),
            priority=priority,
        )


# ── Policy result ─────────────────────────────────────────────────────────────

@dataclass
class PolicyResult:
    """Decision output for a single path + mode query."""
    path: str
    matched_rule: PathAiModeRule | None
    allowed_modes: frozenset[str]
    blocked_modes: frozenset[str]
    allow_hidden_states: bool = True
    allow_logits: bool = True
    allow_attention: bool = True
    allow_free_text_generation: bool = True
    allow_tool_decision_from_model_text: bool = True
    allow_code_generation: bool = True
    require_controlled_write_policy: bool = False
    max_input_chars: int = 0
    max_batch_size: int = 0
    llm_scope: str = ""
    allowed_model_engines: frozenset[str] = field(default_factory=frozenset)
    reason_codes: list[str] = field(default_factory=list)

    def is_mode_allowed(self, mode: str) -> bool:
        """Return True if ``mode`` is not blocked by this policy result."""
        if mode in self.blocked_modes:
            return False
        if self.allowed_modes:
            return mode in self.allowed_modes
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "matched_rule": self.matched_rule.path_glob if self.matched_rule else None,
            "allowed_modes": sorted(self.allowed_modes),
            "blocked_modes": sorted(self.blocked_modes),
            "allow_hidden_states": self.allow_hidden_states,
            "allow_logits": self.allow_logits,
            "allow_attention": self.allow_attention,
            "allow_free_text_generation": self.allow_free_text_generation,
            "allow_tool_decision_from_model_text": self.allow_tool_decision_from_model_text,
            "allow_code_generation": self.allow_code_generation,
            "require_controlled_write_policy": self.require_controlled_write_policy,
            "max_input_chars": self.max_input_chars,
            "max_batch_size": self.max_batch_size,
            "llm_scope": self.llm_scope,
            "allowed_model_engines": sorted(self.allowed_model_engines),
            "reason_codes": self.reason_codes,
        }


_OPEN_RESULT = PolicyResult(
    path="",
    matched_rule=None,
    allowed_modes=frozenset(),
    blocked_modes=frozenset(),
    reason_codes=["default_no_restriction"],
)


# ── Policy service ────────────────────────────────────────────────────────────

class PathAiModePolicyService:
    """Resolves AI mode policy for a given file path.

    Rules are sorted by ``priority`` descending; first matching rule wins.
    Default (no match): all modes allowed, existing behavior unchanged.
    """

    def __init__(self, rules: list[PathAiModeRule] | None = None) -> None:
        self._rules: list[PathAiModeRule] = sorted(
            rules or [], key=lambda r: r.priority, reverse=True
        )

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "PathAiModePolicyService":
        """Build from the top-level Ananta config dict."""
        raw_rules = list((config or {}).get("path_ai_modes") or [])
        rules = [PathAiModeRule.from_raw(r) for r in raw_rules if isinstance(r, dict)]
        return cls(rules=rules)

    def resolve(self, path: str) -> PolicyResult:
        """Return the policy for a given file path (POSIX separators preferred)."""
        normalised = _normalise(path)
        for rule in self._rules:
            if _matches(rule.path_glob, normalised):
                reason = [f"matched_glob:{rule.path_glob}"]
                if rule.blocked_ai_modes:
                    reason.append("has_blocked_modes")
                return PolicyResult(
                    path=path,
                    matched_rule=rule,
                    allowed_modes=rule.allowed_ai_modes,
                    blocked_modes=rule.blocked_ai_modes,
                    allow_hidden_states=rule.allow_hidden_states,
                    allow_logits=rule.allow_logits,
                    allow_attention=rule.allow_attention,
                    allow_free_text_generation=rule.allow_free_text_generation,
                    allow_tool_decision_from_model_text=rule.allow_tool_decision_from_model_text,
                    allow_code_generation=rule.allow_code_generation,
                    require_controlled_write_policy=rule.require_controlled_write_policy,
                    max_input_chars=rule.max_input_chars,
                    max_batch_size=rule.max_batch_size,
                    llm_scope=rule.llm_scope,
                    allowed_model_engines=rule.allowed_model_engines,
                    reason_codes=reason,
                )
        return PolicyResult(
            path=path,
            matched_rule=None,
            allowed_modes=frozenset(),
            blocked_modes=frozenset(),
            reason_codes=["default_no_restriction"],
        )

    def resolve_for_candidates(
        self, paths: list[str]
    ) -> dict[str, PolicyResult]:
        """Batch resolve; returns dict keyed by path."""
        return {p: self.resolve(p) for p in paths}

    def is_mode_allowed(self, path: str, mode: str) -> bool:
        return self.resolve(path).is_mode_allowed(mode)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _matches(glob: str, path: str) -> bool:
    norm_glob = glob.replace("\\", "/").lstrip("/")
    if fnmatch.fnmatch(path, norm_glob):
        return True
    # Also match if path starts with the base of the glob (without /**)
    base = norm_glob.rstrip("/*")
    if base and path.startswith(base):
        return True
    return False


# ── Module singleton ──────────────────────────────────────────────────────────

_policy_service: PathAiModePolicyService | None = None


def get_path_ai_mode_policy_service() -> PathAiModePolicyService:
    global _policy_service
    if _policy_service is None:
        _policy_service = PathAiModePolicyService()
    return _policy_service


def reset_path_ai_mode_policy_service(new: PathAiModePolicyService | None = None) -> None:
    """Replace singleton — useful in tests and config reloads."""
    global _policy_service
    _policy_service = new
