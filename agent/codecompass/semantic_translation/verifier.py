from __future__ import annotations

import re
from typing import Any

from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter
from agent.codecompass.semantic_translation.type_registry import TypeMappingRegistry


class SemanticTranslationVerifier:
    def __init__(self, *, type_registry: TypeMappingRegistry | None = None):
        self.type_registry = type_registry or TypeMappingRegistry()
        self.java_adapter = JavaSemanticAdapter()

    def verify(self, *, source_path: str, source_code: str, target_code: str, transform_artifact: dict[str, Any]) -> dict[str, Any]:
        target_language = str(transform_artifact.get("target_language") or "").lower()
        graph = self.java_adapter.emit_graph_records(source_path, source_code)
        errors: list[dict[str, Any]] = []
        warnings = list(transform_artifact.get("warnings") or [])
        for node in graph["nodes"]:
            attrs = dict(node.get("attributes") or {})
            if ":property:" in node["id"] or attrs.get("kind") not in {"record", "class", "enum"}:
                continue
            if attrs.get("kind") == "enum":
                for value in attrs.get("enum_values") or []:
                    if value not in target_code:
                        errors.append({"code": "missing_enum_value", "source_node": node["id"], "reason": value})
                continue
            for prop in attrs.get("properties") or []:
                if prop["name"] not in target_code:
                    errors.append({"code": "missing_target_property", "source_node": node["id"], "reason": prop["name"]})
                    continue
                mapped = self.type_registry.map_type(prop.get("type", ""), source_language="java", target_language=target_language, policy={})
                target_type = str(mapped.get("target_type") or "")
                if target_type and target_type not in target_code:
                    errors.append({"code": "target_type_mismatch", "source_node": node["id"], "rule_id": mapped.get("rule_id"), "reason": f"{prop['name']}:{target_type}"})
        if errors:
            status = "failed"
        elif transform_artifact.get("status") == "needs_review" or warnings:
            status = "verified_with_warnings"
        else:
            status = "verified"
        if re.search(r"secret|password|token", target_code, re.IGNORECASE):
            warnings.append("target_contains_sensitive_term")
        return {
            "schema": "codecompass_semantic_translation_graph.v1",
            "status": status,
            "errors": errors,
            "warnings": sorted(set(warnings)),
            "source_node_count": len(graph["nodes"]),
            "verified_rule_ids": list(transform_artifact.get("rule_ids") or []),
        }
