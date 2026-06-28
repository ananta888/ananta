from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TypeMapping:
    rule_id: str
    source_language: str
    target_language: str
    source_pattern: str
    target_pattern: str
    semantic_kind: str = "property"
    lossiness: str = "lossless"
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    examples: tuple[dict[str, str], ...] = ()

    def as_rule(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "source_pattern": self.source_pattern,
            "target_pattern": self.target_pattern,
            "semantic_kind": self.semantic_kind,
            "lossiness": self.lossiness,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "warnings": list(self.warnings),
            "examples": list(self.examples),
        }


class TypeMappingRegistry:
    def __init__(self, rules: list[TypeMapping] | None = None):
        self._rules = list(rules or _BUILTIN_RULES)
        self._by_id = {rule.rule_id: rule for rule in self._rules}
        if len(self._by_id) != len(self._rules):
            raise ValueError("duplicate type mapping rule id")

    def rules(self) -> list[TypeMapping]:
        return list(self._rules)

    def get(self, rule_id: str) -> TypeMapping | None:
        return self._by_id.get(str(rule_id or "").strip())

    def map_type(self, source_type: str, *, source_language: str, target_language: str, policy: dict | None = None) -> dict:
        source = normalize_java_type(source_type)
        src_lang = str(source_language or "").lower()
        target_lang = str(target_language or "").lower()
        policy = dict(policy or {})
        for rule in self._rules:
            if rule.source_language != src_lang or rule.target_language != target_lang:
                continue
            mapped = _apply_rule(rule, source, policy)
            if mapped is not None:
                return mapped
        return {
            "status": "needs_review",
            "source_type": source,
            "target_type": "unknown",
            "rule_id": "",
            "lossiness": "unknown",
            "warnings": ["unknown_type_mapping"],
        }

    def find_by_source(self, source_type: str, *, target_languages: list[str] | tuple[str, ...]) -> list[dict]:
        result = []
        for target in target_languages:
            mapped = self.map_type(source_type, source_language="java", target_language=target)
            if mapped["status"] != "unsupported":
                result.append(mapped)
        return result

    def find_by_semantic_kind(self, semantic_kind: str, *, source_language: str = "", target_language: str = "") -> list[TypeMapping]:
        kind = str(semantic_kind or "").strip().lower()
        src = str(source_language or "").strip().lower()
        tgt = str(target_language or "").strip().lower()
        return [
            rule for rule in self._rules
            if rule.semantic_kind == kind
            and (not src or rule.source_language == src)
            and (not tgt or rule.target_language == tgt)
        ]

    def find_by_lossiness(self, lossiness: str, *, source_language: str = "", target_language: str = "") -> list[TypeMapping]:
        loss = str(lossiness or "").strip().lower()
        src = str(source_language or "").strip().lower()
        tgt = str(target_language or "").strip().lower()
        return [
            rule for rule in self._rules
            if rule.lossiness == loss
            and (not src or rule.source_language == src)
            and (not tgt or rule.target_language == tgt)
        ]


def normalize_java_type(source_type: str) -> str:
    value = re.sub(r"\s+", " ", str(source_type or "").strip())
    value = value.replace("? extends ", "").replace("? super ", "")
    return value


def _apply_rule(rule: TypeMapping, source: str, policy: dict) -> dict | None:
    pattern = rule.source_pattern
    if pattern.endswith("<T>"):
        base = pattern[:-3]
        if not source.startswith(base + "<") or not source.endswith(">"):
            return None
        inner = source[len(base) + 1 : -1].strip()
        target = rule.target_pattern.replace("T", inner)
        if rule.rule_id.endswith("optional_to_ts"):
            inner_mapped = TypeMappingRegistry().map_type(inner, source_language="java", target_language="typescript", policy=policy)
            target = f"{inner_mapped.get('target_type', inner)} | undefined"
        elif rule.rule_id.endswith("optional_to_kotlin"):
            if not policy.get("allow_optional_to_nullable", False):
                return {
                    "status": "needs_review",
                    "source_type": source,
                    "target_type": "",
                    "rule_id": rule.rule_id,
                    "lossiness": "policy_guarded",
                    "warnings": ["optional_to_nullable_requires_policy"],
                }
            inner_mapped = TypeMappingRegistry().map_type(inner, source_language="java", target_language="kotlin", policy=policy)
            target = f"{inner_mapped.get('target_type', inner)}?"
        elif rule.rule_id.endswith("list_to_ts") or rule.rule_id.endswith("set_to_ts"):
            inner_mapped = TypeMappingRegistry().map_type(inner, source_language="java", target_language="typescript", policy=policy)
            target = f"{inner_mapped.get('target_type', inner)}[]"
        elif rule.rule_id.endswith("list_to_kotlin"):
            inner_mapped = TypeMappingRegistry().map_type(inner, source_language="java", target_language="kotlin", policy=policy)
            target = f"List<{inner_mapped.get('target_type', inner)}>"
        return _result(rule, source, target)
    if pattern == "Map<K,V>" and source.startswith("Map<") and source.endswith(">"):
        inner = source[4:-1]
        parts = _split_generic_args(inner)
        if len(parts) != 2:
            return _result(rule, source, rule.target_pattern, status="needs_review", extra_warnings=["map_generic_parse_failed"])
        if rule.target_language == "typescript":
            k = TypeMappingRegistry().map_type(parts[0], source_language="java", target_language="typescript", policy=policy)
            v = TypeMappingRegistry().map_type(parts[1], source_language="java", target_language="typescript", policy=policy)
            return _result(rule, source, f"Record<{k.get('target_type')}, {v.get('target_type')}>")
        v = TypeMappingRegistry().map_type(parts[1], source_language="java", target_language="kotlin", policy=policy)
        k = TypeMappingRegistry().map_type(parts[0], source_language="java", target_language="kotlin", policy=policy)
        return _result(rule, source, f"Map<{k.get('target_type')}, {v.get('target_type')}>")
    if source == pattern:
        if rule.rule_id == "java_bigdecimal_to_ts_number" and policy.get("typescript_bigdecimal") == "number":
            return _result(rule, source, "number", extra_warnings=["bigdecimal_to_number_lossy"])
        return _result(rule, source, rule.target_pattern)
    return None


def _result(rule: TypeMapping, source: str, target: str, *, status: str = "ok", extra_warnings: list[str] | None = None) -> dict:
    warnings = [*rule.warnings, *(extra_warnings or [])]
    if rule.lossiness == "lossy" and status == "ok":
        status = "needs_review"
    return {
        "status": status,
        "source_type": source,
        "target_type": target,
        "rule_id": rule.rule_id,
        "lossiness": rule.lossiness,
        "warnings": warnings,
    }


def _split_generic_args(value: str) -> list[str]:
    depth = 0
    current = []
    parts = []
    for ch in value:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


_BUILTIN_RULES = [
    TypeMapping("java_string_to_ts", "java", "typescript", "String", "string"),
    TypeMapping("java_uuid_to_ts", "java", "typescript", "UUID", "string"),
    TypeMapping("java_integer_to_ts", "java", "typescript", "Integer", "number"),
    TypeMapping("java_long_to_ts", "java", "typescript", "Long", "number"),
    TypeMapping("java_int_to_ts", "java", "typescript", "int", "number"),
    TypeMapping("java_boolean_to_ts", "java", "typescript", "Boolean", "boolean"),
    TypeMapping("java_bool_to_ts", "java", "typescript", "boolean", "boolean"),
    TypeMapping("java_bigdecimal_to_ts_number", "java", "typescript", "BigDecimal", "string", lossiness="policy_guarded", warnings=("bigdecimal_preserved_as_string_by_default",)),
    TypeMapping("java_localdate_to_ts", "java", "typescript", "LocalDate", "string", warnings=("date_format_contract_required",)),
    TypeMapping("java_localdatetime_to_ts", "java", "typescript", "LocalDateTime", "string", warnings=("datetime_format_contract_required",)),
    TypeMapping("java_optional_to_ts", "java", "typescript", "Optional<T>", "T | undefined", semantic_kind="optional_absence"),
    TypeMapping("java_list_to_ts", "java", "typescript", "List<T>", "T[]", semantic_kind="collection"),
    TypeMapping("java_set_to_ts", "java", "typescript", "Set<T>", "T[]", semantic_kind="collection", warnings=("set_uniqueness_not_enforced_by_array",)),
    TypeMapping("java_map_to_ts", "java", "typescript", "Map<K,V>", "Record<K, V>", semantic_kind="map"),
    TypeMapping("java_string_to_kotlin", "java", "kotlin", "String", "String"),
    TypeMapping("java_uuid_to_kotlin", "java", "kotlin", "UUID", "String", warnings=("uuid_import_policy_required",)),
    TypeMapping("java_integer_to_kotlin", "java", "kotlin", "Integer", "Int"),
    TypeMapping("java_long_to_kotlin", "java", "kotlin", "Long", "Long"),
    TypeMapping("java_int_to_kotlin", "java", "kotlin", "int", "Int"),
    TypeMapping("java_boolean_to_kotlin", "java", "kotlin", "Boolean", "Boolean"),
    TypeMapping("java_bool_to_kotlin", "java", "kotlin", "boolean", "Boolean"),
    TypeMapping("java_bigdecimal_to_kotlin", "java", "kotlin", "BigDecimal", "BigDecimal"),
    TypeMapping("java_localdate_to_kotlin", "java", "kotlin", "LocalDate", "LocalDate"),
    TypeMapping("java_localdatetime_to_kotlin", "java", "kotlin", "LocalDateTime", "LocalDateTime"),
    TypeMapping("java_optional_to_kotlin", "java", "kotlin", "Optional<T>", "T?", semantic_kind="optional_absence", lossiness="policy_guarded"),
    TypeMapping("java_list_to_kotlin", "java", "kotlin", "List<T>", "List<T>", semantic_kind="collection"),
    TypeMapping("java_set_to_kotlin", "java", "kotlin", "Set<T>", "Set<T>", semantic_kind="collection"),
    TypeMapping("java_map_to_kotlin", "java", "kotlin", "Map<K,V>", "Map<K, V>", semantic_kind="map"),
]
