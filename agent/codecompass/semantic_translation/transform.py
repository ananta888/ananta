from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter
from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRuleRegistry
from agent.codecompass.semantic_translation.models import utc_now_iso
from agent.codecompass.semantic_translation.type_registry import TypeMappingRegistry


@dataclass(frozen=True)
class TransformRequest:
    source_path: str
    source_code: str
    target_language: str
    allowed_rule_ids: tuple[str, ...] = ()
    policy: dict[str, Any] | None = None


class DeterministicTransformEngine:
    def __init__(self, *, type_registry: TypeMappingRegistry | None = None, rule_registry: EquivalenceRuleRegistry | None = None):
        self.type_registry = type_registry or TypeMappingRegistry()
        self.rule_registry = rule_registry or EquivalenceRuleRegistry()
        self.java_adapter = JavaSemanticAdapter()

    def transform(self, request: TransformRequest) -> dict[str, Any]:
        target_language = str(request.target_language or "").lower()
        if target_language not in {"typescript", "kotlin"}:
            return self._artifact(request, status="unsupported", target_code="", warnings=["unsupported_target_language"], nodes=[], rules=[])
        graph = self.java_adapter.emit_graph_records(request.source_path, request.source_code)
        source_types = [node for node in graph["nodes"] if node.get("semantic_kind") in {"data_record", "enum_value", "interface_contract"} and ":property:" not in node["id"] and ":enum:" not in node["id"]]
        rendered: list[str] = []
        warnings = list(_diag_codes(graph.get("diagnostics") or []))
        applied_rules: list[str] = []
        for node in source_types:
            attrs = dict(node.get("attributes") or {})
            transformed = self._transform_type(attrs, target_language, request.policy or {})
            warnings.extend(transformed["warnings"])
            applied_rules.extend(transformed["rule_ids"])
            if transformed["status"] == "needs_review":
                rendered.append(f"/* needs_review: {attrs.get('name')} */")
            if transformed["code"]:
                rendered.append(transformed["code"])
        status = "safe_auto_transform"
        if not rendered:
            status = "unsupported"
            warnings.append("no_supported_source_types")
        elif any("unknown" in warning or "requires" in warning for warning in warnings):
            status = "needs_review"
        target_code = "\n\n".join(rendered).strip() + ("\n" if rendered else "")
        return self._artifact(request, status=status, target_code=target_code, warnings=warnings, nodes=graph["nodes"], rules=sorted(set(applied_rules)))

    def _transform_type(self, attrs: dict[str, Any], target_language: str, policy: dict[str, Any]) -> dict[str, Any]:
        kind = attrs.get("kind")
        name = attrs.get("name")
        warnings: list[str] = []
        rule_ids: list[str] = []
        if kind in {"record", "class"}:
            lines = []
            needs_review = False
            for prop in attrs.get("properties") or []:
                mapped = self.type_registry.map_type(prop.get("type", ""), source_language="java", target_language=target_language, policy=policy)
                warnings.extend(mapped.get("warnings") or [])
                if mapped.get("status") == "needs_review":
                    needs_review = True
                if prop.get("nullability") == "unknown_nullability":
                    warnings.append("unknown_nullability")
                if mapped.get("rule_id"):
                    rule_ids.append(mapped["rule_id"])
                target_type = mapped.get("target_type") or "unknown"
                if target_language == "typescript":
                    optional = "?" if prop.get("nullability") == "optional_absence" else ""
                    lines.append(f"  {prop['name']}{optional}: {target_type};")
                else:
                    nullable_suffix = "?" if prop.get("nullability") == "nullable" else ""
                    lines.append(f"    val {prop['name']}: {target_type}{nullable_suffix}")
            if target_language == "typescript":
                return {"status": "needs_review" if needs_review else "ok", "code": f"export interface {name} {{\n" + "\n".join(lines) + "\n}", "warnings": warnings, "rule_ids": [*rule_ids, "eq.java_record.ts_interface.v1"]}
            comma_lines = ",\n".join(lines)
            return {"status": "needs_review" if needs_review else "ok", "code": f"data class {name}(\n{comma_lines}\n)", "warnings": warnings, "rule_ids": [*rule_ids, "eq.java_record.kotlin_data_class.v1"]}
        if kind == "enum":
            values = attrs.get("enum_values") or []
            if target_language == "typescript":
                body = "\n".join(f"  {value} = '{value}'," for value in values)
                return {"status": "ok", "code": f"export enum {name} {{\n{body}\n}}", "warnings": warnings, "rule_ids": ["eq.java_enum.ts_enum.v1"]}
            return {"status": "ok", "code": f"enum class {name} {{\n  " + ",\n  ".join(values) + "\n}", "warnings": warnings, "rule_ids": ["eq.java_enum.kotlin_enum.v1"]}
        if kind == "interface":
            warnings.append("interface_signature_requires_review")
            if target_language == "typescript":
                return {"status": "needs_review", "code": f"export interface {name} {{\n  // method signatures require review\n}}", "warnings": warnings, "rule_ids": []}
            return {"status": "needs_review", "code": f"interface {name} {{\n  // method signatures require review\n}}", "warnings": warnings, "rule_ids": []}
        return {"status": "unsupported", "code": "", "warnings": ["unsupported_source_kind"], "rule_ids": []}

    def _artifact(self, request: TransformRequest, *, status: str, target_code: str, warnings: list[str], nodes: list[dict], rules: list[str]) -> dict[str, Any]:
        return {
            "schema": "codecompass_semantic_translation_graph.v1",
            "kind": "transform_artifact",
            "artifact_id": _hash("|".join([request.source_path, request.target_language, target_code]))[:16],
            "source_path": request.source_path,
            "target_language": request.target_language,
            "source_hash": _hash(request.source_code),
            "target_hash": _hash(target_code),
            "target_code": target_code,
            "status": status,
            "rule_ids": rules,
            "warnings": sorted(set(warnings)),
            "source_node_count": len(nodes),
            "created_at": utc_now_iso(),
            "_provenance": {"output_kind": "transform_artifacts"},
        }


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _diag_codes(rows: list[dict]) -> list[str]:
    return [str(row.get("code") or "") for row in rows if row.get("code")]
