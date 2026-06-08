"""TaskIntentRouter (te-002) — classifies a task's tool-calls / command into a named intent.

Intent vocabulary
-----------------
read-only deterministic:
  list_files, read_file, grep_search, git_status, git_diff,
  json_validate, schema_validate

hybrid (deterministic execution, but may need LLM for planning):
  run_tests

llm_required (no deterministic handler exists):
  llm_generate, code_review, llm_unknown
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ── Intent literals ───────────────────────────────────────────────────────────

INTENT_LIST_FILES      = "list_files"
INTENT_READ_FILE       = "read_file"
INTENT_GREP_SEARCH     = "grep_search"
INTENT_GIT_STATUS      = "git_status"
INTENT_GIT_DIFF        = "git_diff"
INTENT_JSON_VALIDATE   = "json_validate"
INTENT_SCHEMA_VALIDATE = "schema_validate"
INTENT_RUN_TESTS       = "run_tests"
INTENT_LLM_GENERATE    = "llm_generate"
INTENT_LLM_UNKNOWN     = "llm_unknown"

# All intents that are safe to bypass the LLM for
DETERMINISTIC_INTENTS: frozenset[str] = frozenset({
    INTENT_LIST_FILES,
    INTENT_READ_FILE,
    INTENT_GREP_SEARCH,
    INTENT_GIT_STATUS,
    INTENT_GIT_DIFF,
    INTENT_JSON_VALIDATE,
    INTENT_SCHEMA_VALIDATE,
})

HYBRID_INTENTS: frozenset[str] = frozenset({INTENT_RUN_TESTS})

LLM_INTENTS: frozenset[str] = frozenset({INTENT_LLM_GENERATE, INTENT_LLM_UNKNOWN})


# ── Tool-name → intent mapping ────────────────────────────────────────────────

_TOOL_INTENT: dict[str, str] = {
    # File system
    "list_files":         INTENT_LIST_FILES,
    "list_directory":     INTENT_LIST_FILES,
    "ls":                 INTENT_LIST_FILES,
    "read_file":          INTENT_READ_FILE,
    "cat_file":           INTENT_READ_FILE,
    "view_file":          INTENT_READ_FILE,
    "file_read":          INTENT_READ_FILE,
    "grep_search":        INTENT_GREP_SEARCH,
    "search_files":       INTENT_GREP_SEARCH,
    "grep":               INTENT_GREP_SEARCH,
    "ripgrep":            INTENT_GREP_SEARCH,
    # Git
    "git_status":         INTENT_GIT_STATUS,
    "git_diff":           INTENT_GIT_DIFF,
    # Validation
    "json_validate":      INTENT_JSON_VALIDATE,
    "validate_json":      INTENT_JSON_VALIDATE,
    "schema_validate":    INTENT_SCHEMA_VALIDATE,
    "validate_schema":    INTENT_SCHEMA_VALIDATE,
    # Tests
    "run_tests":          INTENT_RUN_TESTS,
    "pytest":             INTENT_RUN_TESTS,
    "run_pytest":         INTENT_RUN_TESTS,
}

# Command prefix patterns → intent
_CMD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*(ls|find\s|dir\s)", re.I),                INTENT_LIST_FILES),
    (re.compile(r"^\s*(cat|less|head|tail|bat)\s", re.I),       INTENT_READ_FILE),
    (re.compile(r"^\s*(grep|rg|ripgrep|ag)\s", re.I),           INTENT_GREP_SEARCH),
    (re.compile(r"^\s*git\s+status\b", re.I),                   INTENT_GIT_STATUS),
    (re.compile(r"^\s*git\s+diff\b", re.I),                     INTENT_GIT_DIFF),
    (re.compile(r"^\s*(python\s+.*\s+-m\s+json|jq)\b", re.I),  INTENT_JSON_VALIDATE),
    (re.compile(r"^\s*(pytest|python\s+-m\s+pytest)\b", re.I),  INTENT_RUN_TESTS),
]


@dataclass(frozen=True)
class IntentResult:
    intent: str
    task_class: str         # "deterministic" | "hybrid" | "llm_required"
    llm_required: bool
    deterministic_handler_id: str | None
    source: str             # "tool_name" | "command_pattern" | "task_kind" | "default"


class TaskIntentRouter:
    """Classify a task into a named intent and execution class.

    Usage::

        router = TaskIntentRouter()
        result = router.route(task)
        if not result.llm_required:
            # run deterministic handler
    """

    def route(self, task: dict[str, Any]) -> IntentResult:
        # 1. Explicit tool_calls
        for tc in task.get("tool_calls") or []:
            intent = self._tool_call_intent(tc)
            if intent:
                return self._make_result(intent, source="tool_name")

        # 2. command string
        cmd = task.get("command") or ""
        if cmd:
            for pattern, intent in _CMD_PATTERNS:
                if pattern.match(cmd):
                    return self._make_result(intent, source="command_pattern")

        # 3. task_kind heuristic
        kind_intent = self._kind_intent(task.get("task_kind") or task.get("kind") or "")
        if kind_intent:
            return self._make_result(kind_intent, source="task_kind")

        # 4. default → needs LLM
        return self._make_result(INTENT_LLM_UNKNOWN, source="default")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _tool_call_intent(self, tc: Any) -> str | None:
        if isinstance(tc, dict):
            name = (tc.get("name") or tc.get("tool") or tc.get("function", {}).get("name") or "").lower()
        elif isinstance(tc, str):
            name = tc.lower()
        else:
            return None
        return _TOOL_INTENT.get(name)

    def _kind_intent(self, kind: str) -> str | None:
        kind = kind.strip().lower()
        mapping = {
            "list_files":      INTENT_LIST_FILES,
            "read_file":       INTENT_READ_FILE,
            "grep":            INTENT_GREP_SEARCH,
            "grep_search":     INTENT_GREP_SEARCH,
            "git_status":      INTENT_GIT_STATUS,
            "git_diff":        INTENT_GIT_DIFF,
            "json_validate":   INTENT_JSON_VALIDATE,
            "schema_validate": INTENT_SCHEMA_VALIDATE,
            "run_tests":       INTENT_RUN_TESTS,
        }
        return mapping.get(kind)

    @staticmethod
    def _make_result(intent: str, *, source: str) -> IntentResult:
        if intent in DETERMINISTIC_INTENTS:
            task_class = "deterministic"
            llm_required = False
            handler_id = intent
        elif intent in HYBRID_INTENTS:
            task_class = "hybrid"
            llm_required = False
            handler_id = intent
        else:
            task_class = "llm_required"
            llm_required = True
            handler_id = None
        return IntentResult(
            intent=intent,
            task_class=task_class,
            llm_required=llm_required,
            deterministic_handler_id=handler_id,
            source=source,
        )
