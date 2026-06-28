from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
from agent.codecompass.semantic_translation.rust_ownership_policy import RustOwnershipPolicyEngine

_registry = PythonToRustTypeRegistry()
_ownership = RustOwnershipPolicyEngine()


@dataclass
class RustEmitResult:
    source: str
    uses: list[str]
    warnings: list[str]
    needs_review: bool
    artifact_kind: str = "rust_source"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "uses": self.uses,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
            "artifact_kind": self.artifact_kind,
        }


class RustEmitter:
    """Deterministic Rust code emitter for Python-derived semantic nodes."""

    def emit_struct(self, name: str, fields: list[dict], *, frozen: bool = False) -> RustEmitResult:
        warnings: list[str] = []
        uses: set[str] = set()
        needs_review = False
        field_lines: list[str] = []
        derive_attrs = ["Debug", "Clone"]
        if frozen:
            derive_attrs.append("PartialEq")
        for f in fields:
            py_type = f.get("type") or ""
            type_ann = f.get("type_annotation") or {}
            is_optional = type_ann.get("is_optional", False)
            mapped = _registry.map_type(py_type, optional=is_optional)
            uses.update(mapped.uses)
            if mapped.needs_review:
                needs_review = True
            warnings.extend(mapped.warnings)
            own = _ownership.decide_field_ownership(f["name"], mapped.rust_type)
            warnings.extend(own.warnings)
            if own.policy == "lifetime_unknown":
                needs_review = True
            rust_t = mapped.rust_type
            field_lines.append(f"    pub {f['name']}: {rust_t},")
        body = "\n".join(field_lines)
        derive = f"#[derive({', '.join(derive_attrs)})]\n"
        comment = "// WARNING: needs_review — check type mappings and ownership\n" if needs_review else ""
        source = f"{comment}{derive}pub struct {name} {{\n{body}\n}}"
        sorted_uses = sorted(uses)
        if sorted_uses:
            use_block = "\n".join(f"use {u};" for u in sorted_uses) + "\n\n"
        else:
            use_block = ""
        return RustEmitResult(use_block + source, sorted_uses, warnings, needs_review)

    def emit_enum(self, name: str, values: list[str]) -> RustEmitResult:
        variants = "\n".join(f"    {v}," for v in values)
        source = f"#[derive(Debug, Clone, PartialEq)]\npub enum {name} {{\n{variants}\n}}"
        return RustEmitResult(source, [], [], False)

    def emit_function_signature(self, fn: dict) -> RustEmitResult:
        warnings: list[str] = []
        uses: set[str] = set()
        needs_review = False
        params: list[str] = []
        for p in fn.get("parameters") or []:
            if p.get("kind") == "self":
                params.append("&self")
                continue
            if p.get("kind") in ("varargs", "kwargs"):
                warnings.append(f"varargs_kwargs_not_supported: param {p['name']}")
                needs_review = True
                continue
            py_type = p.get("type") or ""
            type_ann = p.get("type_annotation") or {}
            mapped = _registry.map_type(py_type, optional=type_ann.get("is_optional", False))
            uses.update(mapped.uses)
            if mapped.needs_review:
                needs_review = True
            warnings.extend(mapped.warnings)
            own = _ownership.decide_param_ownership(p["name"], mapped.rust_type)
            rust_t = mapped.rust_type
            if own.policy == "borrowed" and not rust_t.startswith("Option") and not rust_t.startswith("&"):
                if rust_t == "String":
                    rust_t = "&str"
                elif rust_t.startswith("Vec<"):
                    elem = rust_t[4:-1]
                    rust_t = f"&[{elem}]"
                else:
                    rust_t = f"&{rust_t}"
            params.append(f"{p['name']}: {rust_t}")
        return_type = fn.get("return_type") or ""
        return_ann = fn.get("return_type_annotation") or {}
        ret_mapped = _registry.map_type(return_type, optional=return_ann.get("is_optional", False))
        uses.update(ret_mapped.uses)
        if ret_mapped.needs_review:
            needs_review = True
        warnings.extend(ret_mapped.warnings)
        rust_ret = ret_mapped.rust_type or "()"
        param_str = ", ".join(params)
        async_prefix = "async " if fn.get("is_async") else ""
        todo_body = "\n    todo!()\n" if not needs_review else "\n    unimplemented!(\"needs_review\")\n"
        source = f"pub {async_prefix}fn {fn['name']}({param_str}) -> {rust_ret} {{{todo_body}}}"
        sorted_uses = sorted(uses)
        return RustEmitResult(source, sorted_uses, warnings, needs_review)
