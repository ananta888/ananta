"""HDE-001/HDE-003: deterministic pre-worker routing tests."""
import pytest

from agent.services.hub_direct_execution_router import (
    REASON_BELOW_CONFIDENCE,
    REASON_DISABLED,
    REASON_MUTATION_INTENT,
    REASON_NO_RULE_MATCH,
    REASON_TOOL_NOT_ALLOWED,
    DirectExecutionDecision,
    HubDirectExecutionRouter,
)


def _cfg(**overrides):
    cfg = {
        "enabled": True,
        "direct_before_worker": True,
        "fallback_to_worker": True,
        "confidence_threshold": 0.8,
        "allowed_tools": [
            "repo.list_files",
            "repo.read_file_range",
            "repo.grep",
            "git.status",
            "git.diff_readonly",
            "test.discover",
            "workspace.diff",
        ],
    }
    cfg.update(overrides)
    return {"hub_direct_execution": cfg}


@pytest.fixture
def router():
    return HubDirectExecutionRouter()


# --- positive examples ------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,tool",
    [
        ("liste dateien", "repo.list_files"),
        ("Liste alle Dateien", "repo.list_files"),
        ("show files", "repo.list_files"),
        ("list files in the repo", "repo.list_files"),
        ("git status", "git.status"),
        ("zeige den git status", "git.status"),
        ("git diff", "git.diff_readonly"),
        ("workspace diff", "workspace.diff"),
        ("welche tests gibt es?", "test.discover"),
        ("discover tests", "test.discover"),
        ("suche nach ToolRoutingService", "repo.grep"),
        ("grep ToolRoutingService", "repo.grep"),
        ("lies agent/config.py", "repo.read_file_range"),
        ("read file agent/config.py lines 10-40", "repo.read_file_range"),
    ],
)
def test_simple_prompts_map_to_tools(router, prompt, tool):
    decision = router.classify(prompt, agent_cfg=_cfg())
    assert decision.eligible, f"{prompt!r} -> {decision.reason_code}"
    assert decision.tool_name == tool
    assert decision.requires_llm is False
    assert decision.confidence >= 0.8


def test_grep_extracts_bounded_pattern_without_shell(router):
    decision = router.classify('suche nach "ToolRoutingService" in agent/*.py', agent_cfg=_cfg())
    assert decision.eligible
    assert decision.arguments["pattern"] == "ToolRoutingService"
    assert decision.arguments["path_globs"] == ["agent/*.py"]
    assert ";" not in str(decision.arguments)


def test_read_file_extracts_line_range(router):
    decision = router.classify("lies agent/config.py zeilen 5 bis 20", agent_cfg=_cfg())
    assert decision.eligible
    assert decision.arguments == {"path": "agent/config.py", "line_start": 5, "line_end": 20}


# --- negative examples (conservative routing) -------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "fix the bug in agent/config.py",
        "implementiere ein neues feature",
        "refactor task_execution_service",
        "lösche alle dateien",
        "analysiere die Architektur und schlage Refactor vor",
        "liste dateien und dann committe alles",
        "run tests",
        "",
        "etwas völlig unklares bitte tun",
    ],
)
def test_write_or_ambiguous_prompts_are_not_eligible(router, prompt):
    decision = router.classify(prompt, agent_cfg=_cfg())
    assert decision.eligible is False
    assert decision.requires_llm is True
    assert decision.tool_name is None


def test_grep_with_shell_metacharacters_is_rejected(router):
    decision = router.classify("suche nach foo; rm -rf /", agent_cfg=_cfg())
    assert decision.eligible is False


def test_long_prompts_are_not_eligible(router):
    decision = router.classify("liste dateien " + "x" * 300, agent_cfg=_cfg())
    assert decision.eligible is False


# --- config gates ------------------------------------------------------------


def test_disabled_config_yields_not_eligible(router):
    decision = router.classify("git status", agent_cfg=_cfg(enabled=False))
    assert decision.eligible is False
    assert decision.reason_code == REASON_DISABLED


def test_tool_outside_allowed_tools_is_rejected(router):
    decision = router.classify("git status", agent_cfg=_cfg(allowed_tools=["repo.grep"]))
    assert decision.eligible is False
    assert decision.reason_code == REASON_TOOL_NOT_ALLOWED


def test_confidence_threshold_filters_matches(router):
    decision = router.classify("lies agent/config.py", agent_cfg=_cfg(confidence_threshold=0.99))
    assert decision.eligible is False
    assert decision.reason_code == REASON_BELOW_CONFIDENCE


def test_no_rule_match_reason_code(router):
    decision = router.classify("wie ist das wetter", agent_cfg=_cfg())
    assert decision.reason_code in {REASON_NO_RULE_MATCH, REASON_MUTATION_INTENT}


def test_decision_serializes_with_schema(router):
    decision = router.classify("git status", agent_cfg=_cfg())
    payload = decision.as_dict()
    assert payload["schema"] == "hub_direct_execution_decision.v1"
    assert payload["eligible"] is True
    assert payload["requires_llm"] is False


# --- dynamic intent aliases (HDE-013/014) ------------------------------------


class _FakeDynamicRegistry:
    def __init__(self, record):
        self._record = record

    def match_intent_alias(self, normalized_prompt):
        spec = dict(self._record.get("spec") or {})
        for alias in spec.get("intent_aliases") or []:
            if str(alias).lower() == normalized_prompt:
                return dict(self._record)
        return None


def _dynamic_record(**spec_overrides):
    spec = {
        "name": "custom.count_todos",
        "intent_aliases": ["zähle todos"],
        "negative_examples": ["lösche todos"],
        "confidence_hint": 0.9,
    }
    spec.update(spec_overrides)
    return {"spec": spec, "status": "active", "approval_status": "granted"}


def test_custom_alias_exact_match_routes_to_dynamic_tool():
    router = HubDirectExecutionRouter(dynamic_registry=_FakeDynamicRegistry(_dynamic_record()))
    cfg = _cfg(allowed_tools=[])
    decision = router.classify("Zähle TODOs", agent_cfg=cfg)
    assert decision.eligible
    assert decision.tool_name == "custom.count_todos"
    assert decision.source == "dynamic"


def test_custom_alias_requires_exact_match():
    router = HubDirectExecutionRouter(dynamic_registry=_FakeDynamicRegistry(_dynamic_record()))
    decision = router.classify("bitte zähle todos im repo und committe", agent_cfg=_cfg(allowed_tools=[]))
    assert decision.eligible is False


def test_negative_example_blocks_dynamic_match():
    record = _dynamic_record(intent_aliases=["lösche todos"], negative_examples=["lösche todos"])
    router = HubDirectExecutionRouter(dynamic_registry=_FakeDynamicRegistry(record))
    decision = router.classify("lösche todos", agent_cfg=_cfg(allowed_tools=[]))
    assert decision.eligible is False
