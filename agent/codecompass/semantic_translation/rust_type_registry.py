from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PYTHON_TO_RUST: dict[str, dict[str, Any]] = {
    "bool": {"rust_type": "bool", "owned": "bool", "lossiness": "lossless", "use": None, "notes": ""},
    "int": {"rust_type": "i64", "owned": "i64", "lossiness": "policy_guarded", "use": None, "notes": "Python int is arbitrary precision. i64 is default; use i128 or num_bigint::BigInt policy for large values."},
    "float": {"rust_type": "f64", "owned": "f64", "lossiness": "lossless", "use": None, "notes": ""},
    "str": {"rust_type": "String", "owned": "String", "lossiness": "lossless", "use": None, "notes": "Owned String by default; use &str policy for borrowed references."},
    "bytes": {"rust_type": "Vec<u8>", "owned": "Vec<u8>", "lossiness": "lossless", "use": None, "notes": ""},
    "Decimal": {"rust_type": "Decimal", "owned": "Decimal", "lossiness": "lossless", "use": "rust_decimal::Decimal", "notes": "Requires rust_decimal crate."},
    "decimal.Decimal": {"rust_type": "Decimal", "owned": "Decimal", "lossiness": "lossless", "use": "rust_decimal::Decimal", "notes": "Requires rust_decimal crate."},
    "datetime": {"rust_type": "DateTime<Utc>", "owned": "DateTime<Utc>", "lossiness": "policy_guarded", "use": "chrono::{DateTime, Utc}", "notes": "Timezone assumed UTC. Use chrono with tz-aware policy."},
    "datetime.datetime": {"rust_type": "DateTime<Utc>", "owned": "DateTime<Utc>", "lossiness": "policy_guarded", "use": "chrono::{DateTime, Utc}", "notes": ""},
    "date": {"rust_type": "NaiveDate", "owned": "NaiveDate", "lossiness": "lossless", "use": "chrono::NaiveDate", "notes": ""},
    "datetime.date": {"rust_type": "NaiveDate", "owned": "NaiveDate", "lossiness": "lossless", "use": "chrono::NaiveDate", "notes": ""},
    "UUID": {"rust_type": "Uuid", "owned": "Uuid", "lossiness": "lossless", "use": "uuid::Uuid", "notes": "Requires uuid crate."},
    "uuid.UUID": {"rust_type": "Uuid", "owned": "Uuid", "lossiness": "lossless", "use": "uuid::Uuid", "notes": ""},
    "None": {"rust_type": "()", "owned": "()", "lossiness": "lossless", "use": None, "notes": "Unit type for void returns. Use Option<T> for nullable."},
    "Any": {"rust_type": "Box<dyn std::any::Any>", "owned": "Box<dyn std::any::Any>", "lossiness": "lossy", "use": None, "notes": "Dynamic typing is not ergonomic in Rust — needs_review."},
}

_COLLECTION_MAPPINGS: dict[str, dict[str, Any]] = {
    "list": {"rust_type": "Vec<{E}>", "use": None, "mutable": True},
    "List": {"rust_type": "Vec<{E}>", "use": None, "mutable": True},
    "set": {"rust_type": "HashSet<{E}>", "use": "std::collections::HashSet", "mutable": True},
    "Set": {"rust_type": "HashSet<{E}>", "use": "std::collections::HashSet", "mutable": True},
    "frozenset": {"rust_type": "HashSet<{E}>", "use": "std::collections::HashSet", "mutable": False, "notes": "Immutability enforced by not exposing &mut."},
    "dict": {"rust_type": "HashMap<{K},{V}>", "use": "std::collections::HashMap", "mutable": True},
    "Dict": {"rust_type": "HashMap<{K},{V}>", "use": "std::collections::HashMap", "mutable": True},
    "tuple": {"rust_type": "({E})", "use": None, "mutable": False, "notes": "Fixed tuple — elements must be mapped individually."},
}


@dataclass
class RustTypeMapping:
    python_type: str
    rust_type: str
    lossiness: str
    uses: list[str]
    warnings: list[str]
    needs_review: bool
    ownership_policy: str = "owned"

    def as_dict(self) -> dict[str, Any]:
        return {
            "python_type": self.python_type,
            "rust_type": self.rust_type,
            "lossiness": self.lossiness,
            "uses": self.uses,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
            "ownership_policy": self.ownership_policy,
        }


class PythonToRustTypeRegistry:
    def map_type(self, python_type: str, *, optional: bool = False, policy: str = "default") -> RustTypeMapping:
        raw = python_type.strip()
        is_optional = optional
        inner = raw

        if raw.startswith("Optional[") and raw.endswith("]"):
            inner = raw[9:-1].strip()
            is_optional = True
        elif " | None" in raw:
            inner = raw.replace(" | None", "").replace("None | ", "").strip()
            is_optional = True
        elif raw == "None":
            return RustTypeMapping("None", "()", "lossless", [], [], False, "owned")

        # Collection types
        for prefix, kind in [("list[", "list"), ("List[", "list"), ("set[", "set"), ("Set[", "set"), ("frozenset[", "frozenset"), ("FrozenSet[", "frozenset")]:
            if inner.startswith(prefix) and inner.endswith("]"):
                elem = inner[len(prefix):-1].strip()
                return self._map_collection(kind, elem, is_optional=is_optional)
        for prefix, kind in [("dict[", "dict"), ("Dict[", "dict")]:
            if inner.startswith(prefix) and inner.endswith("]"):
                kv = inner[len(prefix):-1]
                parts = _split_args(kv)
                if len(parts) == 2:
                    return self._map_dict(parts[0].strip(), parts[1].strip(), is_optional=is_optional)
        for prefix in ("tuple[", "Tuple["):
            if inner.startswith(prefix) and inner.endswith("]"):
                elem = inner[len(prefix):-1].strip()
                return self._map_tuple(elem, is_optional=is_optional)

        if inner in PYTHON_TO_RUST:
            m = PYTHON_TO_RUST[inner]
            rust_t = m["rust_type"]
            if is_optional:
                rust_t = f"Option<{rust_t}>"
            warnings = []
            if m.get("notes"):
                warnings.append(m["notes"])
            if m["lossiness"] == "policy_guarded" and inner == "int" and policy == "default":
                warnings.append("int_precision_policy: Python int mapped to i64 by default; use i128/BigInt policy for large values")
            needs_review = m["lossiness"] == "lossy"
            uses = [m["use"]] if m.get("use") else []
            return RustTypeMapping(inner, rust_t, m["lossiness"], uses, warnings, needs_review, "owned")

        return RustTypeMapping(
            inner, "/* unknown */ Box<dyn std::any::Any>", "unknown", [],
            [f"unknown_python_type:{inner}"], True, "owned"
        )

    def _map_collection(self, kind: str, elem: str, *, is_optional: bool = False) -> RustTypeMapping:
        coll = _COLLECTION_MAPPINGS.get(kind, _COLLECTION_MAPPINGS["list"])
        elem_mapped = self.map_type(elem)
        rust_t = coll["rust_type"].replace("{E}", elem_mapped.rust_type)
        if is_optional:
            rust_t = f"Option<{rust_t}>"
        uses = ([coll["use"]] if coll.get("use") else []) + elem_mapped.uses
        warnings = list(elem_mapped.warnings)
        if coll.get("notes"):
            warnings.append(coll["notes"])
        return RustTypeMapping(kind, rust_t, "lossless" if not elem_mapped.needs_review else "lossy", list(dict.fromkeys(uses)), warnings, elem_mapped.needs_review, "owned")

    def _map_dict(self, key: str, val: str, *, is_optional: bool = False) -> RustTypeMapping:
        km = self.map_type(key)
        vm = self.map_type(val)
        rust_t = f"HashMap<{km.rust_type},{vm.rust_type}>"
        if is_optional:
            rust_t = f"Option<{rust_t}>"
        uses = ["std::collections::HashMap"] + km.uses + vm.uses
        warnings = km.warnings + vm.warnings
        needs_review = km.needs_review or vm.needs_review
        return RustTypeMapping("dict", rust_t, "lossy" if needs_review else "lossless", list(dict.fromkeys(uses)), warnings, needs_review, "owned")

    def _map_tuple(self, elem: str, *, is_optional: bool = False) -> RustTypeMapping:
        parts = _split_args(elem)
        mapped = [self.map_type(p.strip()) for p in parts]
        inner_types = ", ".join(m.rust_type for m in mapped)
        rust_t = f"({inner_types})" if len(parts) > 1 else f"({inner_types},)"
        if is_optional:
            rust_t = f"Option<{rust_t}>"
        uses = []
        for m in mapped:
            uses.extend(m.uses)
        warnings = []
        for m in mapped:
            warnings.extend(m.warnings)
        needs_review = any(m.needs_review for m in mapped)
        return RustTypeMapping("tuple", rust_t, "lossless" if not needs_review else "lossy", list(dict.fromkeys(uses)), warnings, needs_review, "owned")


def _split_args(value: str) -> list[str]:
    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in value:
        if ch in ("<", "[", "("):
            depth += 1
        elif ch in (">", "]", ")"):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts
