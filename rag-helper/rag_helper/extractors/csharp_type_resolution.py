from __future__ import annotations

import re


CSHARP_SYSTEM_TYPES = {
    "Action": "System.Action",
    "DateTime": "System.DateTime",
    "DateOnly": "System.DateOnly",
    "Decimal": "System.Decimal",
    "Exception": "System.Exception",
    "Guid": "System.Guid",
    "IDisposable": "System.IDisposable",
    "List": "System.Collections.Generic.List",
    "Task": "System.Threading.Tasks.Task",
    "Task<T>": "System.Threading.Tasks.Task",
    "TimeOnly": "System.TimeOnly",
}

CSHARP_PRIMITIVES = {
    "bool",
    "byte",
    "char",
    "decimal",
    "double",
    "dynamic",
    "float",
    "int",
    "long",
    "nint",
    "nuint",
    "object",
    "sbyte",
    "short",
    "string",
    "uint",
    "ulong",
    "ushort",
    "var",
    "void",
}


def uniq_keep_order(items: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
            if limit is not None and len(out) >= limit:
                break
    return out


def split_generics(type_text: str) -> list[str]:
    if not type_text:
        return []
    stripped = type_text
    stripped = stripped.replace("global::", "")
    stripped = re.sub(r"\b(ref|out|in|params|scoped|readonly|required)\b", " ", stripped)
    stripped = stripped.replace("?", " ").replace("[", " ").replace("]", " ")
    stripped = stripped.replace("<", " ").replace(">", " ").replace(",", " ")
    tokens = re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", stripped)
    return uniq_keep_order(tokens)


def strip_generics(type_text: str | None) -> str | None:
    if not type_text:
        return type_text
    stripped = re.sub(r"<.*?>", "", type_text)
    stripped = stripped.replace("[]", "").replace("?", "").replace("global::", "").strip()
    return stripped


def short_type_name(type_text: str | None) -> str | None:
    if not type_text:
        return type_text
    raw = strip_generics(type_text) or type_text
    return raw.split(".")[-1].strip()


def parse_using_map(usings: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for using_entry in usings:
        if using_entry.startswith("static:"):
            continue
        if "=" in using_entry:
            alias, target = [part.strip() for part in using_entry.split("=", 1)]
            if alias and target:
                mapping[alias] = target
            continue
        short = using_entry.split(".")[-1]
        mapping[short] = using_entry
    return mapping


def parse_using_namespaces(usings: list[str]) -> list[str]:
    namespaces: list[str] = []
    for using_entry in usings:
        if using_entry.startswith("static:") or "=" in using_entry:
            continue
        namespaces.append(using_entry)
    return uniq_keep_order(namespaces)


def find_resolution_conflicts(type_name: str, resolved_candidates: list[str]) -> list[dict[str, object]]:
    if not type_name or len(resolved_candidates) <= 1:
        return []
    return [{
        "type_name": type_name,
        "candidates": resolved_candidates,
    }]


def uniq_conflicts(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[dict[str, object]] = []
    for item in items:
        key = (str(item.get("type_name") or ""), tuple(item.get("candidates", [])))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def resolve_type_name(
    type_text: str | None,
    namespace_name: str | None,
    using_map: dict[str, str],
    known_namespace_types: dict[str, set[str]],
    same_file_types: set[str],
    using_namespaces: list[str] | None = None,
) -> list[str]:
    if not type_text:
        return []

    using_namespaces = using_namespaces or []
    resolved: list[str] = []
    for candidate in split_generics(type_text):
        if candidate in CSHARP_PRIMITIVES:
            resolved.append(candidate)
            continue

        if "." in candidate:
            outer_name = candidate.split(".", 1)[0]
            if namespace_name and outer_name in same_file_types:
                resolved.append(f"{namespace_name}.{candidate}")
            else:
                resolved.append(candidate)
            continue

        if candidate in using_map:
            resolved.append(using_map[candidate])
            continue

        same_namespace_candidates: list[str] = []
        if namespace_name and candidate in same_file_types:
            same_namespace_candidates.append(f"{namespace_name}.{candidate}")
        elif namespace_name and candidate in known_namespace_types.get(namespace_name, set()):
            same_namespace_candidates.append(f"{namespace_name}.{candidate}")

        using_candidates: list[str] = []
        for using_namespace in using_namespaces:
            if candidate in known_namespace_types.get(using_namespace, set()):
                using_candidates.append(f"{using_namespace}.{candidate}")

        merged_candidates = uniq_keep_order(same_namespace_candidates + using_candidates)
        if merged_candidates:
            resolved.extend(merged_candidates)
            continue

        if candidate in CSHARP_SYSTEM_TYPES:
            resolved.append(CSHARP_SYSTEM_TYPES[candidate])
            continue

        resolved.append(candidate)

    return uniq_keep_order(resolved)
