from __future__ import annotations

import re


JAVA_LANG_TYPES = {
    "String", "Integer", "Long", "Double", "Float", "Boolean", "Short", "Byte",
    "Character", "Object", "Class", "Exception", "RuntimeException", "Throwable",
    "Number", "Void", "System", "Math", "Thread", "StringBuilder", "StringBuffer",
}

JAVA_PRIMITIVES = {
    "byte", "short", "int", "long", "float", "double", "boolean", "char", "void",
}


def uniq_keep_order(items: list[str], limit: int | None = None) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
            if limit is not None and len(out) >= limit:
                break
    return out


def split_generics(type_text: str) -> list[str]:
    if not type_text:
        return []
    s = type_text
    s = re.sub(r"@\w+(\([^)]*\))?", " ", s)
    s = s.replace("?", " ")
    s = re.sub(r"\bextends\b|\bsuper\b", " ", s)
    s = s.replace("[", " ").replace("]", " ")
    s = s.replace("<", " ").replace(">", " ")
    s = s.replace(",", " ")
    tokens = re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", s)
    return uniq_keep_order(tokens)


def strip_generics(type_text: str | None) -> str | None:
    if not type_text:
        return type_text
    s = re.sub(r"<.*?>", "", type_text)
    s = s.replace("[]", "").strip()
    return s


def short_type_name(type_text: str | None) -> str | None:
    if not type_text:
        return type_text
    raw = strip_generics(type_text) or type_text
    raw = raw.split(".")[-1]
    return raw.strip()


def parse_import_map(imports: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for imp in imports:
        if imp.startswith("static:"):
            continue
        if imp.endswith(".*"):
            continue
        short = imp.split(".")[-1]
        mapping[short] = imp
    return mapping


def parse_wildcard_imports(imports: list[str]) -> list[str]:
    wildcard_imports: list[str] = []
    for imp in imports:
        if imp.startswith("static:"):
            continue
        if imp.endswith(".*"):
            wildcard_imports.append(imp[:-2])
    return uniq_keep_order(wildcard_imports)


def find_resolution_conflicts(type_name: str, resolved_candidates: list[str]) -> list[dict[str, object]]:
    if not type_name or len(resolved_candidates) <= 1:
        return []
    return [{
        "type_name": type_name,
        "candidates": resolved_candidates,
    }]


def uniq_conflicts(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    result: list[dict[str, object]] = []
    for item in items:
        key = (
            str(item.get("type_name", "")),
            tuple(item.get("candidates", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def resolve_type_name(
    type_text: str | None,
    package_name: str | None,
    import_map: dict[str, str],
    known_package_types: dict[str, set[str]],
    same_file_types: set[str],
    wildcard_imports: list[str] | None = None,
) -> list[str]:
    if not type_text:
        return []

    resolved: list[str] = []
    candidates = split_generics(type_text)
    wildcard_imports = wildcard_imports or []

    for cand in candidates:
        if cand in JAVA_PRIMITIVES:
            resolved.append(cand)
            continue

        if "." in cand:
            outer_name = cand.split(".", 1)[0]
            if package_name and outer_name in same_file_types:
                resolved.append(f"{package_name}.{cand}")
                continue
            resolved.append(cand)
            continue

        if cand in import_map:
            resolved.append(import_map[cand])
            continue

        same_package_candidates: list[str] = []
        if package_name and cand in same_file_types:
            same_package_candidates.append(f"{package_name}.{cand}")
        elif package_name and cand in known_package_types.get(package_name, set()):
            same_package_candidates.append(f"{package_name}.{cand}")

        wildcard_candidates: list[str] = []
        for wildcard_package in wildcard_imports:
            if cand in known_package_types.get(wildcard_package, set()):
                wildcard_candidates.append(f"{wildcard_package}.{cand}")

        merged_candidates = uniq_keep_order(same_package_candidates + wildcard_candidates)
        if merged_candidates:
            resolved.extend(merged_candidates)
            continue

        if cand in JAVA_LANG_TYPES:
            resolved.append(f"java.lang.{cand}")
            continue

        resolved.append(cand)

    return uniq_keep_order(resolved)
