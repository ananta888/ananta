"""OHA-005: Multi-layer compaction rule loader.

Merge order: builtin → user_global → project_local → task_override.
Project rules may tighten limits but cannot disable security-signal preservation.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BUILTIN_RULES_PATH = Path(__file__).parents[2] / "config" / "tool_output_compaction_rules" / "builtin.json"

# Patterns compiled once from builtin preserve_regex rules are always loaded.
_ALWAYS_PRESERVE_RULE_ID = "preserve_errors"


def _load_json_file(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("tool_output_compaction_rule_loader: failed to load %s — %s", path, exc)
        return None


def _load_project_rules(project_rules_path: str | None) -> list[dict]:
    if not project_rules_path:
        return []
    base = Path(project_rules_path)
    if not base.is_dir():
        return []
    rules: list[dict] = []
    for p in sorted(base.glob("*.json")):
        doc = _load_json_file(p)
        if doc and isinstance(doc.get("rules"), list):
            for rule in doc["rules"]:
                # Project rules may not remove security_signal=True preserve rules.
                if rule.get("security_signal"):
                    logger.warning(
                        "tool_output_compaction_rule_loader: project rule %r claims security_signal=True — ignored",
                        rule.get("id"),
                    )
                    continue
                rules.append(rule)
    return rules


def load_rules(
    *,
    project_rules_path: str | None = None,
    builtin_rules_enabled: bool = True,
) -> "CompactionRuleSet":
    builtin_doc = _load_json_file(_BUILTIN_RULES_PATH) if builtin_rules_enabled else None
    builtin_rules: list[dict] = (builtin_doc or {}).get("rules") or []

    project_rules = _load_project_rules(project_rules_path)

    # Project rules cannot override builtin security_signal rules.
    security_rule_ids = {r["id"] for r in builtin_rules if r.get("security_signal")}
    filtered_project = [r for r in project_rules if r.get("id") not in security_rule_ids]

    merged: list[dict] = list(builtin_rules) + filtered_project
    return CompactionRuleSet(merged)


class CompactionRuleSet:
    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self._rules = rules
        self._preserve_patterns: list[tuple[re.Pattern, str]] = []
        self._truncate_rules: list[dict] = []
        self._dedup_rules: list[dict] = []

        for rule in rules:
            rule_type = str(rule.get("type") or "")
            rule_id = str(rule.get("id") or "")
            try:
                if rule_type == "preserve_regex":
                    flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
                    pat = re.compile(str(rule["pattern"]), flags)
                    self._preserve_patterns.append((pat, rule_id))
                elif rule_type == "keep_first_last":
                    self._truncate_rules.append(rule)
                elif rule_type == "dedup_lines":
                    self._dedup_rules.append(rule)
            except Exception as exc:
                logger.warning("CompactionRuleSet: invalid rule %r — %s", rule_id, exc)

    @property
    def preserve_patterns(self) -> list[tuple[re.Pattern, str]]:
        return self._preserve_patterns

    @property
    def truncate_rules(self) -> list[dict]:
        return self._truncate_rules

    @property
    def dedup_rules(self) -> list[dict]:
        return self._dedup_rules

    def truncate_rule_for_tool(self, tool_name: str) -> dict | None:
        """Return the most specific truncation rule for tool_name."""
        for rule in self._truncate_rules:
            applies = rule.get("applies_to") or ["*"]
            if tool_name in applies:
                return rule
        for rule in self._truncate_rules:
            applies = rule.get("applies_to") or ["*"]
            if "*" in applies:
                return rule
        return None
