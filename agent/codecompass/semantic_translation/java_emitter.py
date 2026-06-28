from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry

_registry = PythonToJavaTypeRegistry()


@dataclass
class JavaEmitResult:
    source: str
    imports: list[str]
    warnings: list[str]
    needs_review: bool
    artifact_kind: str = "java_source"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "imports": self.imports,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
            "artifact_kind": self.artifact_kind,
        }


class JavaEmitter:
    """Deterministic Java code emitter for Python-derived semantic nodes."""

    def emit_record(self, name: str, fields: list[dict]) -> JavaEmitResult:
        warnings: list[str] = []
        imports: set[str] = set()
        needs_review = False
        components: list[str] = []
        for f in fields:
            py_type = f.get("type") or ""
            type_ann = f.get("type_annotation") or {}
            is_optional = type_ann.get("is_optional", False)
            mapped = _registry.map_type(py_type, optional=is_optional)
            imports.update(mapped.imports)
            if mapped.needs_review:
                needs_review = True
            warnings.extend(mapped.warnings)
            java_t = mapped.java_type if mapped.java_type else "Object"
            components.append(f"    {java_t} {f['name']}")
        has_review = needs_review
        body = ",\n".join(components)
        annotation = "    // WARNING: needs_review — check type mappings\n" if has_review else ""
        source = f"{annotation}public record {name}(\n{body}\n) {{}}"
        sorted_imports = sorted(imports)
        if sorted_imports:
            import_block = "\n".join(f"import {i};" for i in sorted_imports) + "\n\n"
        else:
            import_block = ""
        return JavaEmitResult(import_block + source, sorted_imports, warnings, needs_review)

    def emit_class(self, name: str, fields: list[dict], *, mutable: bool = True) -> JavaEmitResult:
        warnings: list[str] = []
        imports: set[str] = set()
        needs_review = False
        field_lines: list[str] = []
        for f in fields:
            py_type = f.get("type") or ""
            type_ann = f.get("type_annotation") or {}
            is_optional = type_ann.get("is_optional", False)
            mapped = _registry.map_type(py_type, optional=is_optional)
            imports.update(mapped.imports)
            if mapped.needs_review:
                needs_review = True
            warnings.extend(mapped.warnings)
            modifier = "" if mutable else "final "
            java_t = mapped.java_type or "Object"
            field_lines.append(f"    private {modifier}{java_t} {f['name']};")
        body = "\n".join(field_lines)
        prefix = "    // WARNING: needs_review\n" if needs_review else ""
        source = f"public class {name} {{\n{prefix}{body}\n}}"
        sorted_imports = sorted(imports)
        if sorted_imports:
            import_block = "\n".join(f"import {i};" for i in sorted_imports) + "\n\n"
        else:
            import_block = ""
        return JavaEmitResult(import_block + source, sorted_imports, warnings, needs_review)

    def emit_enum(self, name: str, values: list[str]) -> JavaEmitResult:
        body = ",\n    ".join(values)
        source = f"public enum {name} {{\n    {body}\n}}"
        return JavaEmitResult(source, [], [], False)

    def emit_method_signature(self, class_name: str, method: dict) -> JavaEmitResult:
        warnings: list[str] = []
        imports: set[str] = set()
        needs_review = False
        params: list[str] = []
        for p in method.get("parameters") or []:
            if p.get("kind") == "self":
                continue
            if p.get("kind") in ("varargs", "kwargs"):
                warnings.append(f"varargs_kwargs_not_supported: param {p['name']}")
                needs_review = True
                continue
            py_type = p.get("type") or ""
            type_ann = p.get("type_annotation") or {}
            mapped = _registry.map_type(py_type, optional=type_ann.get("is_optional", False))
            imports.update(mapped.imports)
            if mapped.needs_review:
                needs_review = True
            warnings.extend(mapped.warnings)
            java_t = mapped.java_type or "Object"
            params.append(f"{java_t} {p['name']}")
        return_type = method.get("return_type") or ""
        return_ann = method.get("return_type_annotation") or {}
        ret_mapped = _registry.map_type(return_type, optional=return_ann.get("is_optional", False))
        imports.update(ret_mapped.imports)
        if ret_mapped.needs_review:
            needs_review = True
        warnings.extend(ret_mapped.warnings)
        java_ret = ret_mapped.java_type or "void"
        param_str = ", ".join(params)
        comment = "    // TODO: body not supported — needs_review\n    " if needs_review else "    "
        source = f"public {java_ret} {method['name']}({param_str}) {{\n{comment}throw new UnsupportedOperationException();\n}}"
        sorted_imports = sorted(imports)
        return JavaEmitResult(source, sorted_imports, warnings, needs_review)
