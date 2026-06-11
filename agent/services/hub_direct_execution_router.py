"""HDE-001/HDE-003: deterministic pre-worker routing for simple hub tasks.

The router sits in front of the worker/LLM (HDE-DD-001): it classifies a
prompt with conservative, deterministic rules and — when a request is
safely answerable by a registered tool — emits a
``DirectExecutionDecision`` so no worker LLM has to be started. The
router only *decides*; authorization stays with the policy gate and
execution is dispatched to the WorkerRuntime by the control plane
(``agent/services/hub_tool_execution_adapter.py``, HDW-DD-001).

Anything ambiguous, multi-step or write-ish yields ``eligible=False``
so the existing worker/LLM path takes over (HDE-DD-005). Dynamic custom
tools are matched only via their explicit ``intent_aliases`` (HDE-014),
never via free-text guessing.

Contract: ``docs/contracts/hub-direct-execution.md``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

REASON_DISABLED = "hub_direct_disabled"
REASON_EMPTY_PROMPT = "empty_prompt"
REASON_MUTATION_INTENT = "mutation_or_complex_intent"
REASON_PROMPT_TOO_LONG = "prompt_too_long"
REASON_NO_RULE_MATCH = "no_rule_match"
REASON_TOOL_NOT_ALLOWED = "tool_not_in_allowed_tools"
REASON_BELOW_CONFIDENCE = "below_confidence_threshold"
REASON_RULE_MATCH = "deterministic_rule_match"
REASON_CUSTOM_ALIAS_MATCH = "custom_tool_intent_alias_match"
REASON_NEGATIVE_EXAMPLE = "custom_tool_negative_example"

_MAX_DIRECT_PROMPT_CHARS = 240
_MAX_PATTERN_CHARS = 200

# Write-ish / multi-step keywords keep the router conservative (HDE-003):
# any hit means "not directly eligible", the worker/LLM decides instead.
_NON_DIRECT_KEYWORDS = (
    "fix", "behebe", "repariere", "implement", "implementiere", "refactor",
    "refaktor", "schreibe", "write ", "erstelle", "create ", "lege ",
    "ändere", "aendere", "change ", "modify", "lösche", "loesche",
    "delete", "remove", "entferne", "patch", "commit", "push", "merge",
    "install", "deploy", "starte", "restart", "update ", "upgrade",
    "führe tests aus", "fuehre tests aus", "run tests", "run the tests",
    "analysiere", "analyze", "analyse", "bewerte", "review", "schlage",
    "propose", "und dann", "danach", "anschließend", "anschliessend",
)


@dataclass(frozen=True)
class DirectExecutionDecision:
    """Outcome of the pre-worker classification (HDE-001).

    ``requires_llm`` is the inverse of ``eligible``: a direct decision
    never needs an LLM, a non-eligible one falls back to the worker.
    """

    eligible: bool
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""
    confidence: float = 0.0
    requires_llm: bool = True
    source: str = "static"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "hub_direct_execution_decision.v1",
            "eligible": self.eligible,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "requires_llm": self.requires_llm,
            "source": self.source,
        }


def _not_eligible(reason_code: str, *, confidence: float = 0.0) -> DirectExecutionDecision:
    return DirectExecutionDecision(eligible=False, reason_code=reason_code, confidence=confidence, requires_llm=True)


def _normalize(prompt: str | None) -> str:
    return re.sub(r"\s+", " ", str(prompt or "").strip().lower())


def _bounded_pattern(raw: str) -> str | None:
    """Strip quotes and cap length; never pass through raw shell commands."""
    value = str(raw or "").strip().strip("'\"`").strip()
    if not value or len(value) > _MAX_PATTERN_CHARS:
        return None
    if any(ch in value for ch in (";", "|", "&", "$(", "`", "\n")):
        return None
    return value


_PATH_RE = r"(?P<path>[\w./\-]+\.[\w]+)"

# Rules match case-insensitively against the whitespace-normalized,
# case-preserving prompt so extracted arguments (grep patterns, paths)
# keep their original casing.
_LIST_FILES_RE = re.compile(
    r"^(liste|zeige|list|show)( mir)?( bitte)?( alle)?( the)? (dateien|files)( im workspace| in the workspace| im repo| in the repo)?( in (?P<glob>[\w./*\-]+))?$",
    re.IGNORECASE,
)
_READ_FILE_RE = re.compile(
    r"^(lies|lese|zeige|read|show|open|cat)( mir)?( bitte)?( die)?( datei| file)? " + _PATH_RE +
    r"( (zeile[n]?|lines?) (?P<start>\d+)( ?(bis|to|-) ?(?P<end>\d+))?)?$",
    re.IGNORECASE,
)
_GREP_RE = re.compile(
    r"^(grep|suche|search|finde|find)( bitte)?( nach| for)? (?P<pattern>.+?)( (in|im) (?P<glob>[\w./*\-]+))?$",
    re.IGNORECASE,
)
_GIT_STATUS_RE = re.compile(r"^(zeige |show )?(den |the )?git[ \-]?status( des workspace| of the workspace)?$", re.IGNORECASE)
_GIT_DIFF_RE = re.compile(r"^(zeige |show )?(den |the )?git[ \-]?diff( (für|fuer|for|von|of) " + _PATH_RE + r")?$", re.IGNORECASE)
_TEST_DISCOVER_RE = re.compile(
    r"^((entdecke|finde|liste|discover|list|find) (die |alle |all )?tests( im workspace| in the workspace)?|welche tests gibt es( im workspace)?\??)$",
    re.IGNORECASE,
)
_WORKSPACE_DIFF_RE = re.compile(r"^(zeige |show )?(den |the )?workspace[ \-]?diff$", re.IGNORECASE)


class HubDirectExecutionRouter:
    """Maps simple prompts onto registered tools without an LLM (HDE-001).

    ``dynamic_registry`` is optional and only used for exact
    ``intent_aliases`` matches of active custom tools (HDE-013/014).
    """

    def __init__(self, dynamic_registry: Any | None = None) -> None:
        self._dynamic_registry = dynamic_registry

    def classify(
        self,
        prompt: str | None,
        task: dict[str, Any] | None = None,
        agent_cfg: dict[str, Any] | None = None,
    ) -> DirectExecutionDecision:
        cfg = self._config(agent_cfg)
        if not bool(cfg.get("enabled", False)):
            return _not_eligible(REASON_DISABLED)
        cased = re.sub(r"\s+", " ", str(prompt or "").strip())
        normalized = cased.lower()
        if not normalized:
            return _not_eligible(REASON_EMPTY_PROMPT)
        if len(normalized) > _MAX_DIRECT_PROMPT_CHARS:
            return _not_eligible(REASON_PROMPT_TOO_LONG)

        custom = self._match_custom_alias(normalized, cfg)
        if custom is not None:
            return self._finalize(custom, cfg)

        if any(keyword in normalized for keyword in _NON_DIRECT_KEYWORDS):
            return _not_eligible(REASON_MUTATION_INTENT)

        matched = self._match_static_rules(cased)
        if matched is None:
            return _not_eligible(REASON_NO_RULE_MATCH)
        return self._finalize(matched, cfg)

    # -- rule matching -----------------------------------------------------

    def _match_static_rules(self, normalized: str) -> DirectExecutionDecision | None:
        match = _GIT_STATUS_RE.match(normalized)
        if match:
            return self._decision("git.status", {}, confidence=0.95)
        match = _WORKSPACE_DIFF_RE.match(normalized)
        if match:
            return self._decision("workspace.diff", {}, confidence=0.95)
        match = _GIT_DIFF_RE.match(normalized)
        if match:
            arguments: dict[str, Any] = {}
            if match.group("path"):
                arguments["path"] = match.group("path")
            return self._decision("git.diff_readonly", arguments, confidence=0.9)
        match = _TEST_DISCOVER_RE.match(normalized)
        if match:
            return self._decision("test.discover", {"limit": 100}, confidence=0.9)
        match = _LIST_FILES_RE.match(normalized)
        if match:
            arguments = {"limit": 200}
            if match.group("glob"):
                arguments["path_glob"] = match.group("glob")
            return self._decision("repo.list_files", arguments, confidence=0.9)
        match = _READ_FILE_RE.match(normalized)
        if match:
            arguments = {"path": match.group("path")}
            if match.group("start"):
                arguments["line_start"] = int(match.group("start"))
                arguments["line_end"] = int(match.group("end") or int(match.group("start")) + 200)
            return self._decision("repo.read_file_range", arguments, confidence=0.85)
        match = _GREP_RE.match(normalized)
        if match:
            pattern = _bounded_pattern(match.group("pattern"))
            if pattern is None:
                return None
            arguments = {"pattern": pattern, "limit": 50}
            if match.group("glob"):
                arguments["path_globs"] = [match.group("glob")]
            return self._decision("repo.grep", arguments, confidence=0.85)
        return None

    def _match_custom_alias(self, normalized: str, cfg: dict[str, Any]) -> DirectExecutionDecision | None:
        """HDE-014: exact intent-alias matches of active dynamic tools only."""
        registry = self._dynamic_registry
        try:
            if registry is None:
                from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

                registry = get_dynamic_tool_registry_service()
            record = registry.match_intent_alias(normalized)
        except Exception:
            return None
        if not record:
            return None
        spec = dict(record.get("spec") or record)
        for negative in spec.get("negative_examples") or []:
            if _normalize(str(negative)) and _normalize(str(negative)) in normalized:
                return None
        confidence = float(spec.get("confidence_hint") or 0.85)
        return DirectExecutionDecision(
            eligible=True,
            tool_name=str(spec.get("name") or ""),
            arguments=dict(record.get("alias_arguments") or {}),
            reason_code=REASON_CUSTOM_ALIAS_MATCH,
            confidence=confidence,
            requires_llm=False,
            source="dynamic",
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _decision(tool_name: str, arguments: dict[str, Any], *, confidence: float) -> DirectExecutionDecision:
        return DirectExecutionDecision(
            eligible=True,
            tool_name=tool_name,
            arguments=arguments,
            reason_code=REASON_RULE_MATCH,
            confidence=confidence,
            requires_llm=False,
        )

    @staticmethod
    def _config(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = (agent_cfg or {}).get("hub_direct_execution")
        return dict(cfg) if isinstance(cfg, dict) else {}

    def _finalize(self, decision: DirectExecutionDecision, cfg: dict[str, Any]) -> DirectExecutionDecision:
        allowed = [str(item or "").strip() for item in (cfg.get("allowed_tools") or []) if str(item or "").strip()]
        if allowed and decision.tool_name not in allowed:
            return _not_eligible(REASON_TOOL_NOT_ALLOWED, confidence=decision.confidence)
        threshold = float(cfg.get("confidence_threshold") or 0.8)
        if decision.confidence < threshold:
            return _not_eligible(REASON_BELOW_CONFIDENCE, confidence=decision.confidence)
        return decision


hub_direct_execution_router = HubDirectExecutionRouter()


def get_hub_direct_execution_router() -> HubDirectExecutionRouter:
    return hub_direct_execution_router
