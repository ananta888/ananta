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


def resolve_type_name(
    type_text: str | None,
    package_name: str | None,
    import_map: dict[str, str],
    known_package_types: dict[str, set[str]],
    same_file_types: set[str],
) -> list[str]:
    if not type_text:
        return []

    resolved: list[str] = []
    candidates = split_generics(type_text)

    for cand in candidates:
        if cand in JAVA_PRIMITIVES:
            resolved.append(cand)
            continue

        if "." in cand:
            resolved.append(cand)
            continue

        if cand in import_map:
            resolved.append(import_map[cand])
            continue

        if cand in same_file_types and package_name:
            resolved.append(f"{package_name}.{cand}")
            continue

        if package_name and cand in known_package_types.get(package_name, set()):
            resolved.append(f"{package_name}.{cand}")
            continue

        if cand in JAVA_LANG_TYPES:
            resolved.append(f"java.lang.{cand}")
            continue

        resolved.append(cand)

    return uniq_keep_order(resolved)
