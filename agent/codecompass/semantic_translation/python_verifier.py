from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry

_java_registry = PythonToJavaTypeRegistry()
_rust_registry = PythonToRustTypeRegistry()


@dataclass
class VerificationResult:
    symbol: str
    target_language: str
    status: str
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "target_language": self.target_language,
            "status": self.status,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "warnings": self.warnings,
        }


class PythonToJavaVerifier:
    """Verifies Python → Java transform artifacts."""

    def verify(self, source_item: dict, java_artifact: dict) -> VerificationResult:
        name = source_item.get("name", "?")
        result = VerificationResult(symbol=name, target_language="java", status="verified")
        source = java_artifact.get("source", "")

        # 1. Field completeness
        for f in source_item.get("fields") or []:
            fname = f["name"]
            if fname not in source:
                result.checks_failed.append(f"missing_field:{fname}")
            else:
                result.checks_passed.append(f"field_present:{fname}")

        # 2. Enum value completeness
        for v in source_item.get("enum_values") or []:
            if v not in source:
                result.checks_failed.append(f"missing_enum_value:{v}")
            else:
                result.checks_passed.append(f"enum_value_present:{v}")

        # 3. Type mappings — check for needs_review in mapped fields
        for f in source_item.get("fields") or []:
            py_type = f.get("type") or ""
            type_ann = f.get("type_annotation") or {}
            if py_type:
                mapped = _java_registry.map_type(py_type, optional=type_ann.get("is_optional", False))
                if mapped.needs_review:
                    result.warnings.append(f"needs_review_type_mapping:{f['name']}:{py_type}->{mapped.java_type}")

        # 4. Optionality — check None defaults don't become non-nullable
        for f in source_item.get("fields") or []:
            type_ann = f.get("type_annotation") or {}
            if type_ann.get("is_optional") and type_ann.get("none_model") == "default_none":
                # Check that the Java artifact uses Optional or nullable
                fname = f["name"]
                if f"Optional<" not in source and "@Nullable" not in source:
                    result.warnings.append(f"optional_field_may_lose_nullability:{fname}")

        # 5. Lost defaults check
        for f in source_item.get("fields") or []:
            if f.get("has_default") and f.get("default") is not None and "factory" in str(f.get("default", "")):
                result.warnings.append(f"default_factory_lost_in_java:{f['name']}")

        # 6. Nullability for Java — record check
        if source_item.get("kind") in ("dataclass", "frozen_dataclass") and "record" in source:
            result.checks_passed.append("java_record_emitted_for_dataclass")
        elif source_item.get("kind") in ("dataclass", "frozen_dataclass") and "class" in source:
            result.checks_passed.append("java_class_emitted_for_dataclass")

        # 7. Exception policy in methods
        for m in source_item.get("methods") or []:
            for w in m.get("warnings") or []:
                if "varargs" in w or "kwargs" in w:
                    result.warnings.append(f"method_{m['name']}_varargs_not_translated")

        if result.checks_failed:
            result.status = "failed"
        elif result.warnings:
            result.status = "verified_with_warnings"
        else:
            result.status = "verified"
        return result


class PythonToRustVerifier:
    """Verifies Python → Rust transform artifacts."""

    def verify(self, source_item: dict, rust_artifact: dict) -> VerificationResult:
        name = source_item.get("name", "?")
        result = VerificationResult(symbol=name, target_language="rust", status="verified")
        source = rust_artifact.get("source", "")

        # 1. Field completeness
        for f in source_item.get("fields") or []:
            fname = f["name"]
            if fname not in source:
                result.checks_failed.append(f"missing_field:{fname}")
            else:
                result.checks_passed.append(f"field_present:{fname}")

        # 2. Enum value completeness
        for v in source_item.get("enum_values") or []:
            if v not in source:
                result.checks_failed.append(f"missing_enum_value:{v}")
            else:
                result.checks_passed.append(f"enum_value_present:{v}")

        # 3. Option<T> for optional fields
        for f in source_item.get("fields") or []:
            type_ann = f.get("type_annotation") or {}
            if type_ann.get("is_optional"):
                fname = f["name"]
                if f"Option<" not in source:
                    result.checks_failed.append(f"option_not_emitted_for_optional_field:{fname}")
                else:
                    result.checks_passed.append(f"option_emitted_for_optional_field:{fname}")

        # 4. Ownership decisions — warn if lifetime_unknown
        for item in rust_artifact.get("warnings") or []:
            if "lifetime" in item:
                result.warnings.append(f"ownership_issue:{item}")

        # 5. Mutability — frozen dataclass should not have mut
        if source_item.get("kind") == "frozen_dataclass":
            if "mut " in source:
                result.warnings.append("frozen_dataclass_has_mut_field")

        # 6. Numeric precision
        for f in source_item.get("fields") or []:
            if f.get("type") in ("int", "float"):
                py_type = f["type"]
                mapped = _rust_registry.map_type(py_type)
                for w in mapped.warnings:
                    result.warnings.append(f"numeric_precision_field_{f['name']}:{w}")

        if result.checks_failed:
            result.status = "failed"
        elif result.warnings:
            result.status = "verified_with_warnings"
        else:
            result.status = "verified"
        return result
