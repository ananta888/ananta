from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RULES_FILE = Path(__file__).parent / "equivalence_rules.v1.json"


@dataclass(frozen=True)
class EquivalenceRule:
    rule_id: str
    scope: str
    source_language: str
    target_language: str
    semantic_kind: str
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    examples: tuple[dict[str, str], ...]
    tests: tuple[str, ...]
    status: str = "stable"
    experimental: bool = False
    deprecated: bool = False
    known_deviations: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "schema": "codecompass_semantic_translation_graph.v1",
            "kind": "equivalence_rule",
            "rule_id": self.rule_id,
            "scope": self.scope,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "semantic_kind": self.semantic_kind,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "examples": list(self.examples),
            "tests": list(self.tests),
            "status": self.status,
            "experimental": self.experimental,
            "deprecated": self.deprecated,
            "known_deviations": list(self.known_deviations),
            "_provenance": {"output_kind": "equivalence_rules"},
        }


def load_rules_from_file(path: Path | None = None) -> list[EquivalenceRule]:
    source = path or _RULES_FILE
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return list(BUILTIN_EQUIVALENCE_RULES)
    result = []
    for entry in list(raw or []):
        try:
            result.append(EquivalenceRule(
                rule_id=str(entry["rule_id"]),
                scope=str(entry.get("scope") or ""),
                source_language=str(entry.get("source_language") or ""),
                target_language=str(entry.get("target_language") or ""),
                semantic_kind=str(entry.get("semantic_kind") or ""),
                preconditions=tuple(str(p) for p in list(entry.get("preconditions") or [])),
                postconditions=tuple(str(p) for p in list(entry.get("postconditions") or [])),
                examples=tuple(dict(ex) for ex in list(entry.get("examples") or [])),
                tests=tuple(str(t) for t in list(entry.get("tests") or [])),
                status=str(entry.get("status") or "stable"),
                experimental=bool(entry.get("experimental", False)),
                deprecated=bool(entry.get("deprecated", False)),
                known_deviations=tuple(str(d) for d in list(entry.get("known_deviations") or [])),
            ))
        except (KeyError, TypeError):
            continue
    return result or list(BUILTIN_EQUIVALENCE_RULES)


class EquivalenceRuleRegistry:
    def __init__(self, rules: list[EquivalenceRule] | None = None, *, rules_file: Path | None = None):
        if rules is not None:
            self._rules = list(rules)
        else:
            self._rules = load_rules_from_file(rules_file)
        self.validate()

    def validate(self) -> None:
        seen = set()
        for rule in self._rules:
            if rule.rule_id in seen:
                raise ValueError(f"duplicate equivalence rule id: {rule.rule_id}")
            seen.add(rule.rule_id)
            if not rule.tests:
                raise ValueError(f"equivalence rule missing tests: {rule.rule_id}")
            if rule.experimental and rule.status == "stable":
                raise ValueError(f"experimental rule cannot be stable: {rule.rule_id}")

    def records(self, *, include_experimental: bool = False) -> list[dict[str, Any]]:
        return [
            rule.as_record()
            for rule in self._rules
            if not rule.deprecated and (include_experimental or not rule.experimental)
        ]

    def find(self, *, source_language: str, target_language: str, semantic_kind: str) -> list[EquivalenceRule]:
        return [
            rule
            for rule in self._rules
            if not rule.deprecated
            and not rule.experimental
            and rule.source_language == source_language
            and rule.target_language == target_language
            and rule.semantic_kind == semantic_kind
        ]


BUILTIN_EQUIVALENCE_RULES = [
    EquivalenceRule("eq.java_record.ts_interface.v1", "dto", "java", "typescript", "data_record", ("all_properties_mapped",), ("target_interface_has_same_property_names",), ({"source": "record User(String name)", "target": "export interface User { name: string; }"},), ("golden:java_record_to_ts_interface",)),
    EquivalenceRule("eq.java_record.kotlin_data_class.v1", "dto", "java", "kotlin", "data_record", ("all_properties_mapped", "nullability_reviewed"), ("target_data_class_has_same_constructor_properties",), ({"source": "record User(String name)", "target": "data class User(val name: String)"},), ("golden:java_record_to_kotlin_data_class",)),
    EquivalenceRule("eq.java_enum.ts_enum.v1", "enum", "java", "typescript", "enum_value", ("enum_values_known",), ("target_enum_preserves_values",), ({"source": "enum Status { ACTIVE }", "target": "export enum Status { ACTIVE = 'ACTIVE' }"},), ("golden:java_enum_to_ts_enum",)),
    EquivalenceRule("eq.java_enum.kotlin_enum.v1", "enum", "java", "kotlin", "enum_value", ("enum_values_known",), ("target_enum_preserves_values",), ({"source": "enum Status { ACTIVE }", "target": "enum class Status { ACTIVE }"},), ("golden:java_enum_to_kotlin_enum",)),
]
