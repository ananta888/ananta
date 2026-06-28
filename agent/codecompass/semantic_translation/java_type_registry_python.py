from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PYTHON_TO_JAVA: dict[str, dict[str, Any]] = {
    "bool": {"java_type": "boolean", "boxed": "Boolean", "lossiness": "lossless", "import": None, "notes": ""},
    "int": {"java_type": "long", "boxed": "Long", "lossiness": "policy_guarded", "import": None, "notes": "Python int is arbitrary precision; Java long is 64-bit. Use BigInteger policy for arbitrary precision."},
    "float": {"java_type": "double", "boxed": "Double", "lossiness": "lossless", "import": None, "notes": "IEEE 754 double — matches Python float semantics."},
    "str": {"java_type": "String", "boxed": "String", "lossiness": "lossless", "import": None, "notes": ""},
    "bytes": {"java_type": "byte[]", "boxed": "byte[]", "lossiness": "lossless", "import": None, "notes": ""},
    "Decimal": {"java_type": "BigDecimal", "boxed": "BigDecimal", "lossiness": "lossless", "import": "java.math.BigDecimal", "notes": ""},
    "decimal.Decimal": {"java_type": "BigDecimal", "boxed": "BigDecimal", "lossiness": "lossless", "import": "java.math.BigDecimal", "notes": ""},
    "datetime": {"java_type": "LocalDateTime", "boxed": "LocalDateTime", "lossiness": "policy_guarded", "import": "java.time.LocalDateTime", "notes": "Timezone not preserved — use ZonedDateTime policy if tz-aware."},
    "datetime.datetime": {"java_type": "LocalDateTime", "boxed": "LocalDateTime", "lossiness": "policy_guarded", "import": "java.time.LocalDateTime", "notes": "Timezone not preserved — use ZonedDateTime policy if tz-aware."},
    "date": {"java_type": "LocalDate", "boxed": "LocalDate", "lossiness": "lossless", "import": "java.time.LocalDate", "notes": ""},
    "datetime.date": {"java_type": "LocalDate", "boxed": "LocalDate", "lossiness": "lossless", "import": "java.time.LocalDate", "notes": ""},
    "UUID": {"java_type": "UUID", "boxed": "UUID", "lossiness": "lossless", "import": "java.util.UUID", "notes": ""},
    "uuid.UUID": {"java_type": "UUID", "boxed": "UUID", "lossiness": "lossless", "import": "java.util.UUID", "notes": ""},
    "None": {"java_type": "void", "boxed": "Void", "lossiness": "lossless", "import": None, "notes": "Only valid as return type — use Optional<T> or @Nullable for nullable fields."},
    "Any": {"java_type": "Object", "boxed": "Object", "lossiness": "lossy", "import": None, "notes": "Dynamic typing lost — needs_review."},
}

_COLLECTION_MAPPINGS: dict[str, dict[str, Any]] = {
    "list": {"java_type": "List<{E}>", "mutable": True, "import": "java.util.List", "impl": "java.util.ArrayList"},
    "List": {"java_type": "List<{E}>", "mutable": True, "import": "java.util.List", "impl": "java.util.ArrayList"},
    "set": {"java_type": "Set<{E}>", "mutable": True, "import": "java.util.Set", "impl": "java.util.HashSet"},
    "Set": {"java_type": "Set<{E}>", "mutable": True, "import": "java.util.Set", "impl": "java.util.HashSet"},
    "frozenset": {"java_type": "Set<{E}>", "mutable": False, "import": "java.util.Set", "impl": "java.util.Collections.unmodifiableSet", "notes": "Immutability must be enforced at construction site."},
    "dict": {"java_type": "Map<{K},{V}>", "mutable": True, "import": "java.util.Map", "impl": "java.util.HashMap"},
    "Dict": {"java_type": "Map<{K},{V}>", "mutable": True, "import": "java.util.Map", "impl": "java.util.HashMap"},
    "tuple": {"java_type": "List<{E}>", "mutable": False, "import": "java.util.List", "notes": "Fixed-length tuple → consider record or Pair policy."},
}


@dataclass
class JavaTypeMapping:
    python_type: str
    java_type: str
    boxed: str
    lossiness: str
    imports: list[str]
    warnings: list[str]
    needs_review: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "python_type": self.python_type,
            "java_type": self.java_type,
            "boxed": self.boxed,
            "lossiness": self.lossiness,
            "imports": self.imports,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


class PythonToJavaTypeRegistry:
    def map_type(self, python_type: str, *, optional: bool = False, policy: str = "default") -> JavaTypeMapping:
        raw = python_type.strip()
        # Handle Optional[T] / T | None unwrapping
        is_optional = optional
        inner = raw
        if raw.startswith("Optional[") and raw.endswith("]"):
            inner = raw[9:-1].strip()
            is_optional = True
        elif " | None" in raw:
            inner = raw.replace(" | None", "").replace("None | ", "").strip()
            is_optional = True
        elif raw == "None":
            return JavaTypeMapping("None", "void", "Void", "lossless", [], [], False)

        # Collection types
        for prefix, kind in [("list[", "list"), ("List[", "list"), ("set[", "set"), ("Set[", "set"), ("frozenset[", "frozenset"), ("FrozenSet[", "frozenset")]:
            if inner.startswith(prefix) and inner.endswith("]"):
                elem = inner[len(prefix):-1].strip()
                return self._map_collection(kind, elem_type=elem, is_optional=is_optional)
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

        # Primitive / known
        if inner in PYTHON_TO_JAVA:
            m = PYTHON_TO_JAVA[inner]
            java_t = m["boxed"] if is_optional else m["java_type"]
            if is_optional:
                java_t = f"Optional<{m['boxed']}>" if m["java_type"] != "void" else "Optional.empty()"
            warnings = []
            if m.get("notes"):
                warnings.append(m["notes"])
            lossy = m["lossiness"] == "lossy"
            policy_guarded = m["lossiness"] == "policy_guarded" and policy == "default"
            needs_review = lossy or (inner == "Any")
            if policy_guarded and inner == "int":
                # Allow long by default, note the precision risk
                warnings.append("int_precision_policy: Python int is arbitrary precision; mapped to long — use BigInteger policy if needed")
            imports = [m["import"]] if m.get("import") else []
            if is_optional and java_t.startswith("Optional"):
                imports.append("java.util.Optional")
            return JavaTypeMapping(python_type=inner, java_type=java_t, boxed=m["boxed"], lossiness=m["lossiness"], imports=imports, warnings=warnings, needs_review=needs_review)

        # Unknown
        return JavaTypeMapping(
            python_type=inner,
            java_type="Object",
            boxed="Object",
            lossiness="unknown",
            imports=[],
            warnings=[f"unknown_python_type:{inner}"],
            needs_review=True,
        )

    def _map_collection(self, kind: str, elem_type: str, *, is_optional: bool = False) -> JavaTypeMapping:
        coll = _COLLECTION_MAPPINGS.get(kind, _COLLECTION_MAPPINGS["list"])
        elem_mapped = self.map_type(elem_type)
        java_t = coll["java_type"].replace("{E}", elem_mapped.boxed)
        if is_optional:
            java_t = f"Optional<{java_t}>"
        imports = [coll["import"]] + elem_mapped.imports
        if is_optional:
            imports.append("java.util.Optional")
        warnings = list(elem_mapped.warnings)
        if coll.get("notes"):
            warnings.append(coll["notes"])
        return JavaTypeMapping(kind, java_t, java_t, "lossless" if not elem_mapped.needs_review else "lossy", list(dict.fromkeys(imports)), warnings, elem_mapped.needs_review)

    def _map_dict(self, key_type: str, val_type: str, *, is_optional: bool = False) -> JavaTypeMapping:
        km = self.map_type(key_type)
        vm = self.map_type(val_type)
        java_t = f"Map<{km.boxed},{vm.boxed}>"
        if is_optional:
            java_t = f"Optional<{java_t}>"
        imports = ["java.util.Map"] + km.imports + vm.imports
        if is_optional:
            imports.append("java.util.Optional")
        warnings = km.warnings + vm.warnings
        needs_review = km.needs_review or vm.needs_review
        return JavaTypeMapping("dict", java_t, java_t, "lossy" if needs_review else "lossless", list(dict.fromkeys(imports)), warnings, needs_review)

    def _map_tuple(self, elem: str, *, is_optional: bool = False) -> JavaTypeMapping:
        parts = _split_args(elem)
        if len(parts) == 1:
            elem_mapped = self.map_type(parts[0].strip())
            java_t = f"List<{elem_mapped.boxed}>"
        else:
            # Fixed-length tuple → List<Object> or record policy
            java_t = "List<Object>"
        if is_optional:
            java_t = f"Optional<{java_t}>"
        imports = ["java.util.List"]
        if is_optional:
            imports.append("java.util.Optional")
        return JavaTypeMapping("tuple", java_t, java_t, "policy_guarded", imports, ["fixed_tuple_consider_record_policy"], False)


def _split_args(value: str) -> list[str]:
    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in value:
        if ch in ("<", "["):
            depth += 1
        elif ch in (">", "]"):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts
