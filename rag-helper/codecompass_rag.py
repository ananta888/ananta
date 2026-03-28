#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


from lxml import etree
from tree_sitter import Language, Parser
import tree_sitter_java as tsjava


DEFAULT_EXTENSIONS = {"java", "xml", "xsd"}
DEFAULT_EXCLUDES = {
    ".git", ".idea", ".vscode", "target", "build", "dist", "out",
    "node_modules", ".gradle", ".settings", ".mvn"
}

JAVA_LANG_TYPES = {
    "String", "Integer", "Long", "Double", "Float", "Boolean", "Short", "Byte",
    "Character", "Object", "Class", "Exception", "RuntimeException", "Throwable",
    "Number", "Void", "System", "Math", "Thread", "StringBuilder", "StringBuffer"
}

JAVA_PRIMITIVES = {
    "byte", "short", "int", "long", "float", "double", "boolean", "char", "void"
}


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def safe_id(*parts: str) -> str:
    return sha1_text("::".join(parts))[:16]


def read_text_file(path: Path) -> str | None:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            pass
    return None


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def collect_files(root: Path, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in DEFAULT_EXCLUDES for part in p.parts):
            continue
        ext = p.suffix.lower().lstrip(".")
        if ext in extensions:
            files.append(p)
    return sorted(files)


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [x for x in lines if x]
    return "\n".join(lines)


def compact_code_snippet(text: str, max_len: int = 2200) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_len:
        return text[:max_len] + "\n...[truncated]..."
    return text


def node_text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def first_child_of_type(node, typ: str):
    for c in node.children:
        if c.type == typ:
            return c
    return None


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
    """
    Zerlegt grob:
      List<UserDto> -> ["List", "UserDto"]
      Map<String, List<User>> -> ["Map", "String", "List", "User"]
    """
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


def looks_like_getter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("get") and len(params) == 0 and return_type not in (None, "void")


def looks_like_boolean_getter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("is") and len(params) == 0 and return_type in ("boolean", "Boolean")


def looks_like_setter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("set") and len(params) == 1 and (return_type is None or return_type == "void")


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


def detect_type_roles(
    type_name: str,
    type_kind: str,
    annotations: list[str],
    imports: list[str],
    fields: list[dict[str, Any]],
    methods: list[dict[str, Any]],
) -> dict[str, Any]:
    ann_text = " ".join(annotations)
    import_text = " ".join(imports)

    field_count = len(fields)
    method_count = len(methods)

    is_lombok = any(x.startswith("@Data") or x.startswith("@Getter") or x.startswith("@Setter")
                    or x.startswith("@Builder") or x.startswith("@Value")
                    or x.startswith("@NoArgsConstructor") or x.startswith("@AllArgsConstructor")
                    or x.startswith("@RequiredArgsConstructor")
                    for x in annotations) or "lombok." in import_text

    is_entity = any(x.startswith("@Entity") or x.startswith("@Embeddable") or x.startswith("@MappedSuperclass")
                    or x.startswith("@Table") for x in annotations)

    is_repository = (
        type_name.endswith("Repository")
        or any(x.startswith("@Repository") for x in annotations)
    )

    is_controller = (
        type_name.endswith("Controller")
        or any(x.startswith("@Controller") or x.startswith("@RestController") for x in annotations)
    )

    is_service = (
        type_name.endswith("Service")
        or any(x.startswith("@Service") for x in annotations)
    )

    method_names = [m["name"] for m in methods]
    trivial_methods = [m for m in methods if m.get("is_trivial")]
    is_dto = (
        type_name.endswith("Dto")
        or type_name.endswith("DTO")
        or type_name.endswith("Request")
        or type_name.endswith("Response")
        or (
            not is_entity
            and not is_repository
            and not is_controller
            and not is_service
            and field_count > 0
            and method_count > 0
            and len(trivial_methods) >= max(1, int(method_count * 0.5))
        )
    )

    is_record_like = (
        type_kind == "record"
        or (
            field_count > 0
            and len(trivial_methods) >= max(1, int(method_count * 0.7))
            and not is_service
            and not is_controller
            and not is_repository
        )
    )

    return {
        "is_lombok": is_lombok,
        "is_entity": is_entity,
        "is_repository": is_repository,
        "is_controller": is_controller,
        "is_service": is_service,
        "is_dto": is_dto,
        "is_record_like": is_record_like,
        "role_labels": [
            label for label, enabled in [
                ("lombok", is_lombok),
                ("entity", is_entity),
                ("repository", is_repository),
                ("controller", is_controller),
                ("service", is_service),
                ("dto", is_dto),
                ("record_like", is_record_like),
            ] if enabled
        ]
    }


class JavaExtractor:
    def __init__(
        self,
        include_code_snippets: bool = True,
        exclude_trivial_methods: bool = False,
    ) -> None:
        self.include_code_snippets = include_code_snippets
        self.exclude_trivial_methods = exclude_trivial_methods
        self.parser = Parser()
        self.parser.language = Language(tsjava.language())

    def pre_scan_types(self, rel_path: str, text: str) -> dict[str, Any]:
        src = text.encode("utf-8", errors="ignore")
        tree = self.parser.parse(src)
        root = tree.root_node

        package_name = None
        type_names: list[str] = []
        imports: list[str] = []

        for child in root.children:
            if child.type == "package_declaration":
                package_name = node_text(src, child).replace("package", "").replace(";", "").strip()
            elif child.type == "import_declaration":
                txt = node_text(src, child).replace("import", "").replace(";", "").strip()
                txt = txt.replace("static ", "static:")
                imports.append(txt)
            elif child.type in {
                "class_declaration", "interface_declaration", "enum_declaration",
                "record_declaration", "annotation_type_declaration"
            }:
                ident = self._extract_identifier(child, src)
                if ident:
                    type_names.append(ident)

        return {
            "file": rel_path,
            "package": package_name,
            "imports": imports,
            "type_names": type_names,
        }

    def parse(
        self,
        rel_path: str,
        text: str,
        known_package_types: dict[str, set[str]],
    ) -> tuple[list[dict], list[dict], list[dict], dict]:
        src = text.encode("utf-8", errors="ignore")
        tree = self.parser.parse(src)
        root = tree.root_node

        index_records: list[dict] = []
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        package_name = self._extract_package(root, src)
        imports = self._extract_imports(root, src)
        import_map = parse_import_map(imports)

        same_file_types: set[str] = set()
        for child in root.children:
            if child.type in {
                "class_declaration", "interface_declaration", "enum_declaration",
                "record_declaration", "annotation_type_declaration"
            }:
                ident = self._extract_identifier(child, src)
                if ident:
                    same_file_types.add(ident)

        type_records = []
        type_names = []
        method_count = 0
        constructor_count = 0

        for child in root.children:
            if child.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                t_index, t_detail, t_relations, t_stats = self._extract_type(
                    rel_path=rel_path,
                    src=src,
                    node=child,
                    package_name=package_name,
                    imports=imports,
                    import_map=import_map,
                    known_package_types=known_package_types,
                    same_file_types=same_file_types,
                )
                index_records.append(t_index)
                detail_records.extend(t_detail)
                relation_records.extend(t_relations)

                type_records.append({
                    "name": t_index["name"],
                    "type_kind": t_index["type_kind"],
                    "method_count": t_stats["method_count"],
                    "constructor_count": t_stats["constructor_count"],
                    "field_count": t_stats["field_count"],
                    "role_labels": t_index["role_labels"],
                })
                type_names.append(t_index["name"])
                method_count += t_stats["method_count"]
                constructor_count += t_stats["constructor_count"]

        file_embedding = (
            f"Java file {rel_path}. "
            f"Package {package_name or 'default'}. "
            f"Contains types: {', '.join(type_names[:20]) or 'none'}. "
            f"Imports count {len(imports)}. Method count {method_count}. "
            f"Constructor count {constructor_count}."
        )

        file_record = {
            "kind": "java_file",
            "file": rel_path,
            "id": f"java_file:{safe_id(rel_path)}",
            "package": package_name,
            "imports": imports,
            "types": type_records,
            "embedding_text": file_embedding,
            "summary": {
                "imports_count": len(imports),
                "type_count": len(type_records),
                "method_count": method_count,
                "constructor_count": constructor_count,
            },
        }
        index_records.insert(0, file_record)

        stats = {
            "kind": "java",
            "file": rel_path,
            "package": package_name,
            "imports_count": len(imports),
            "type_count": len(type_records),
            "method_count": method_count,
            "constructor_count": constructor_count,
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats

    def _extract_package(self, root, src: bytes) -> str | None:
        for child in root.children:
            if child.type == "package_declaration":
                return node_text(src, child).replace("package", "").replace(";", "").strip()
        return None

    def _extract_imports(self, root, src: bytes) -> list[str]:
        result = []
        for child in root.children:
            if child.type == "import_declaration":
                txt = node_text(src, child)
                txt = txt.replace("import", "").replace(";", "").strip()
                txt = txt.replace("static ", "static:")
                result.append(txt)
        return result

    def _extract_modifiers(self, node, src: bytes) -> list[str]:
        mod_node = first_child_of_type(node, "modifiers")
        if not mod_node:
            return []
        return [x.strip() for x in node_text(src, mod_node).split() if x.strip() and not x.strip().startswith("@")]

    def _extract_annotations(self, node, src: bytes) -> list[str]:
        mod_node = first_child_of_type(node, "modifiers")
        anns = []
        if mod_node:
            for c in mod_node.children:
                if c.type == "annotation":
                    anns.append(node_text(src, c).strip())
        return anns

    def _extract_identifier(self, node, src: bytes) -> str | None:
        ident = first_child_of_type(node, "identifier")
        if ident:
            return node_text(src, ident).strip()
        return None

    def _extract_return_type(self, node, src: bytes) -> str | None:
        for c in node.children:
            if c.type in {
                "type_identifier", "generic_type", "integral_type", "floating_point_type",
                "boolean_type", "void_type", "array_type", "scoped_type_identifier",
                "annotated_type"
            }:
                return node_text(src, c).strip()
        return None

    def _extract_field(self, node, src: bytes) -> dict[str, Any]:
        mods = self._extract_modifiers(node, src)
        anns = self._extract_annotations(node, src)
        typ = None
        declarators = []

        for c in node.children:
            if c.type in {
                "type_identifier", "generic_type", "integral_type", "floating_point_type",
                "boolean_type", "array_type", "scoped_type_identifier", "annotated_type"
            } and typ is None:
                typ = node_text(src, c).strip()
            elif c.type == "variable_declarator":
                declarators.append(normalize_ws(node_text(src, c)))

        return {
            "type": typ,
            "declarators": declarators,
            "modifiers": mods,
            "annotations": anns,
        }

    def _extract_parameters(self, node, src: bytes) -> list[str]:
        params = []
        fp = first_child_of_type(node, "formal_parameters")
        if fp:
            for c in fp.children:
                if c.type == "formal_parameter":
                    params.append(normalize_ws(node_text(src, c)))
        return params

    def _extract_method_calls(self, node, src: bytes) -> list[str]:
        if node is None:
            return []
        calls = []
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type == "method_invocation":
                calls.append(normalize_ws(node_text(src, cur)))
            stack.extend(reversed(cur.children))
        return uniq_keep_order([c[:250] for c in calls], limit=200)

    def _extract_type_refs(self, node, src: bytes) -> list[str]:
        refs = []
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type in {
                "type_identifier", "generic_type", "scoped_type_identifier",
                "array_type", "annotated_type"
            }:
                refs.append(normalize_ws(node_text(src, cur)))
            stack.extend(reversed(cur.children))
        return uniq_keep_order(refs, limit=200)

    def _extract_extends_implements(self, node, src: bytes) -> tuple[str | None, list[str]]:
        extends = None
        implements = []

        for c in node.children:
            if c.type == "superclass":
                extends = normalize_ws(node_text(src, c).replace("extends", ""))
            elif c.type == "super_interfaces":
                raw = normalize_ws(node_text(src, c).replace("implements", ""))
                implements = [x.strip() for x in raw.split(",") if x.strip()]

        return extends, implements

    def _make_relation(
        self,
        file: str,
        source_id: str,
        source_kind: str,
        source_name: str,
        relation: str,
        target: str,
        target_resolved: str | None = None,
        weight: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "kind": "relation",
            "file": file,
            "id": f"relation:{safe_id(file, source_id, relation, target, target_resolved or '')}",
            "source_id": source_id,
            "source_kind": source_kind,
            "source_name": source_name,
            "relation": relation,
            "target": target,
            "target_resolved": target_resolved,
            "weight": weight,
        }
        if extra:
            payload.update(extra)
        return payload

    def _extract_type(
        self,
        rel_path: str,
        src: bytes,
        node,
        package_name: str | None,
        imports: list[str],
        import_map: dict[str, str],
        known_package_types: dict[str, set[str]],
        same_file_types: set[str],
    ) -> tuple[dict, list[dict], list[dict], dict]:
        name = self._extract_identifier(node, src) or "<anonymous>"
        type_kind = node.type.replace("_declaration", "")
        modifiers = self._extract_modifiers(node, src)
        annotations = self._extract_annotations(node, src)
        extends, implements = self._extract_extends_implements(node, src)

        body = first_child_of_type(node, "class_body")
        if body is None and type_kind == "record":
            for c in node.children:
                if c.type == "class_body":
                    body = c
                    break

        fields: list[dict[str, Any]] = []
        method_signatures: list[str] = []
        constructor_signatures: list[str] = []
        detail_records: list[dict] = []
        relation_records: list[dict] = []
        method_indexes: list[dict[str, Any]] = []

        field_type_resolved: list[str] = []
        called_methods: list[str] = []
        used_types_resolved: list[str] = []
        type_raw_refs: list[str] = []

        type_id = f"java_type:{safe_id(rel_path, name, type_kind)}"

        if body:
            for member in body.children:
                if member.type == "field_declaration":
                    field = self._extract_field(member, src)
                    field["resolved_types"] = resolve_type_name(
                        field.get("type"),
                        package_name=package_name,
                        import_map=import_map,
                        known_package_types=known_package_types,
                        same_file_types=same_file_types,
                    )
                    fields.append(field)
                    field_type_resolved.extend(field["resolved_types"])

                    for raw_t in split_generics(field.get("type") or ""):
                        resolved = resolve_type_name(
                            raw_t, package_name, import_map, known_package_types, same_file_types
                        )
                        for rt in resolved:
                            relation_records.append(self._make_relation(
                                file=rel_path,
                                source_id=type_id,
                                source_kind="java_type",
                                source_name=name,
                                relation="field_type_uses",
                                target=raw_t,
                                target_resolved=rt,
                            ))

                elif member.type == "method_declaration":
                    m_index, m_detail, m_relations, m_meta = self._extract_method(
                        rel_path=rel_path,
                        class_name=name,
                        node=member,
                        src=src,
                        package_name=package_name,
                        import_map=import_map,
                        known_package_types=known_package_types,
                        same_file_types=same_file_types,
                    )
                    if self.exclude_trivial_methods and m_meta["is_trivial"]:
                        continue
                    method_signatures.append(m_index["signature"])
                    called_methods.extend(m_index["calls"])
                    used_types_resolved.extend(m_index["resolved_type_refs"])
                    type_raw_refs.extend(m_index["type_refs"])
                    method_indexes.append(m_index)
                    detail_records.append(m_index)
                    detail_records.append(m_detail)
                    relation_records.extend(m_relations)

                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=type_id,
                        source_kind="java_type",
                        source_name=name,
                        relation="declares_method",
                        target=m_index["name"],
                        target_resolved=m_index["id"],
                    ))

                elif member.type == "constructor_declaration":
                    c_index, c_detail, c_relations = self._extract_constructor(
                        rel_path=rel_path,
                        class_name=name,
                        node=member,
                        src=src,
                        package_name=package_name,
                        import_map=import_map,
                        known_package_types=known_package_types,
                        same_file_types=same_file_types,
                    )
                    constructor_signatures.append(c_index["signature"])
                    called_methods.extend(c_index["calls"])
                    used_types_resolved.extend(c_index["resolved_type_refs"])
                    type_raw_refs.extend(c_index["type_refs"])
                    detail_records.append(c_index)
                    detail_records.append(c_detail)
                    relation_records.extend(c_relations)

                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=type_id,
                        source_kind="java_type",
                        source_name=name,
                        relation="declares_constructor",
                        target=c_index["signature"],
                        target_resolved=c_index["id"],
                    ))

        roles = detect_type_roles(
            type_name=name,
            type_kind=type_kind,
            annotations=annotations,
            imports=imports,
            fields=fields,
            methods=method_indexes,
        )

        extends_resolved = resolve_type_name(
            extends, package_name, import_map, known_package_types, same_file_types
        )
        implements_resolved = []
        for impl in implements:
            implements_resolved.extend(resolve_type_name(
                impl, package_name, import_map, known_package_types, same_file_types
            ))

        used_types_resolved = uniq_keep_order(
            used_types_resolved + field_type_resolved + extends_resolved + implements_resolved, limit=80
        )
        called_methods = uniq_keep_order(called_methods, limit=50)

        if extends:
            for rt in extends_resolved or [extends]:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=type_id,
                    source_kind="java_type",
                    source_name=name,
                    relation="extends",
                    target=extends,
                    target_resolved=rt,
                ))

        for impl in implements:
            impl_resolved = resolve_type_name(
                impl, package_name, import_map, known_package_types, same_file_types
            )
            for rt in impl_resolved or [impl]:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=type_id,
                    source_kind="java_type",
                    source_name=name,
                    relation="implements",
                    target=impl,
                    target_resolved=rt,
                ))

        embedding_text = (
            f"Java {type_kind} {name} in file {rel_path}. "
            f"Package {package_name or 'default'}. "
            f"Roles: {', '.join(roles['role_labels']) or 'none'}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Annotations: {', '.join(annotations) or 'none'}. "
            f"Extends {extends or 'none'}. Implements {', '.join(implements) or 'none'}. "
            f"Methods: {', '.join(method_signatures[:20]) or 'none'}. "
            f"Used types: {', '.join(used_types_resolved[:20]) or 'none'}. "
            f"Calls: {', '.join(called_methods[:20]) or 'none'}."
        )

        type_record = {
            "kind": "java_type",
            "file": rel_path,
            "id": type_id,
            "package": package_name,
            "imports": imports,
            "name": name,
            "type_kind": type_kind,
            "modifiers": modifiers,
            "annotations": annotations,
            "extends": extends,
            "extends_resolved": extends_resolved,
            "implements": implements,
            "implements_resolved": implements_resolved,
            "fields": fields[:50],
            "methods": method_signatures[:200],
            "constructors": constructor_signatures[:50],
            "used_types": used_types_resolved,
            "called_methods": called_methods,
            "role_labels": roles["role_labels"],
            "roles": roles,
            "embedding_text": embedding_text,
            "summary": (
                f"{type_kind} {name}; methods={len(method_signatures)}; "
                f"constructors={len(constructor_signatures)}; fields={len(fields)}; "
                f"roles={','.join(roles['role_labels']) or 'none'}"
            ),
        }

        stats = {
            "field_count": len(fields),
            "method_count": len(method_signatures),
            "constructor_count": len(constructor_signatures),
        }

        return type_record, detail_records, relation_records, stats

    def _extract_method(
        self,
        rel_path: str,
        class_name: str,
        node,
        src: bytes,
        package_name: str | None,
        import_map: dict[str, str],
        known_package_types: dict[str, set[str]],
        same_file_types: set[str],
    ) -> tuple[dict, dict, list[dict], dict]:
        name = self._extract_identifier(node, src) or "<method>"
        modifiers = self._extract_modifiers(node, src)
        annotations = self._extract_annotations(node, src)
        params = self._extract_parameters(node, src)
        return_type = self._extract_return_type(node, src)
        body = first_child_of_type(node, "block")

        signature = f"{name}({', '.join(params)})"
        if return_type:
            signature += f": {return_type}"

        calls = self._extract_method_calls(body, src)
        type_refs = self._extract_type_refs(node, src)
        resolved_type_refs: list[str] = []
        for tr in type_refs:
            resolved_type_refs.extend(resolve_type_name(
                tr, package_name, import_map, known_package_types, same_file_types
            ))
        resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

        is_getter = looks_like_getter(name, params, return_type)
        is_bool_getter = looks_like_boolean_getter(name, params, return_type)
        is_setter = looks_like_setter(name, params, return_type)
        is_trivial = is_getter or is_bool_getter or is_setter

        method_id = f"java_method:{safe_id(rel_path, class_name, signature)}"

        embedding_text = (
            f"Java method {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Annotations: {', '.join(annotations) or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}. "
            f"Trivial accessor: {'yes' if is_trivial else 'no'}."
        )

        idx = {
            "kind": "java_method",
            "file": rel_path,
            "id": method_id,
            "class": class_name,
            "name": name,
            "signature": signature,
            "return_type": return_type,
            "resolved_return_types": resolve_type_name(
                return_type, package_name, import_map, known_package_types, same_file_types
            ),
            "parameters": params,
            "modifiers": modifiers,
            "annotations": annotations,
            "parameter_count": len(params),
            "calls": calls[:30],
            "type_refs": type_refs[:30],
            "resolved_type_refs": resolved_type_refs,
            "is_getter": is_getter or is_bool_getter,
            "is_setter": is_setter,
            "is_trivial": is_trivial,
            "embedding_text": embedding_text,
        }

        detail = {
            "kind": "java_method_detail",
            "file": rel_path,
            "id": f"java_method_detail:{safe_id(rel_path, class_name, signature)}",
            "class": class_name,
            "name": name,
            "signature": signature,
            "return_type": return_type,
            "resolved_return_types": idx["resolved_return_types"],
            "parameters": params,
            "modifiers": modifiers,
            "annotations": annotations,
            "calls": calls,
            "type_refs": type_refs,
            "resolved_type_refs": resolved_type_refs,
            "is_getter": is_getter or is_bool_getter,
            "is_setter": is_setter,
            "is_trivial": is_trivial,
            "embedding_text": embedding_text,
        }
        if self.include_code_snippets:
            detail["code_snippet"] = compact_code_snippet(node_text(src, node), max_len=3200)

        relations: list[dict] = []
        for raw_t in type_refs:
            for rt in resolve_type_name(raw_t, package_name, import_map, known_package_types, same_file_types):
                relations.append(self._make_relation(
                    file=rel_path,
                    source_id=method_id,
                    source_kind="java_method",
                    source_name=f"{class_name}.{name}",
                    relation="uses_type",
                    target=raw_t,
                    target_resolved=rt,
                ))

        if return_type:
            for rt in idx["resolved_return_types"] or [return_type]:
                relations.append(self._make_relation(
                    file=rel_path,
                    source_id=method_id,
                    source_kind="java_method",
                    source_name=f"{class_name}.{name}",
                    relation="returns",
                    target=return_type,
                    target_resolved=rt,
                ))

        for call in calls:
            relations.append(self._make_relation(
                file=rel_path,
                source_id=method_id,
                source_kind="java_method",
                source_name=f"{class_name}.{name}",
                relation="calls",
                target=call,
                target_resolved=None,
            ))

        meta = {"is_trivial": is_trivial}
        return idx, detail, relations, meta

    def _extract_constructor(
        self,
        rel_path: str,
        class_name: str,
        node,
        src: bytes,
        package_name: str | None,
        import_map: dict[str, str],
        known_package_types: dict[str, set[str]],
        same_file_types: set[str],
    ) -> tuple[dict, dict, list[dict]]:
        name = self._extract_identifier(node, src) or class_name
        modifiers = self._extract_modifiers(node, src)
        annotations = self._extract_annotations(node, src)
        params = self._extract_parameters(node, src)
        body = first_child_of_type(node, "constructor_body")

        signature = f"{name}({', '.join(params)})"
        calls = self._extract_method_calls(body, src)
        type_refs = self._extract_type_refs(node, src)

        resolved_type_refs: list[str] = []
        for tr in type_refs:
            resolved_type_refs.extend(resolve_type_name(
                tr, package_name, import_map, known_package_types, same_file_types
            ))
        resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

        ctor_id = f"java_constructor:{safe_id(rel_path, class_name, signature)}"

        embedding_text = (
            f"Java constructor {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}."
        )

        idx = {
            "kind": "java_constructor",
            "file": rel_path,
            "id": ctor_id,
            "class": class_name,
            "name": name,
            "signature": signature,
            "parameters": params,
            "modifiers": modifiers,
            "annotations": annotations,
            "parameter_count": len(params),
            "calls": calls[:30],
            "type_refs": type_refs[:30],
            "resolved_type_refs": resolved_type_refs,
            "embedding_text": embedding_text,
        }

        detail = {
            "kind": "java_constructor_detail",
            "file": rel_path,
            "id": f"java_constructor_detail:{safe_id(rel_path, class_name, signature)}",
            "class": class_name,
            "name": name,
            "signature": signature,
            "parameters": params,
            "modifiers": modifiers,
            "annotations": annotations,
            "calls": calls,
            "type_refs": type_refs,
            "resolved_type_refs": resolved_type_refs,
            "embedding_text": embedding_text,
        }
        if self.include_code_snippets:
            detail["code_snippet"] = compact_code_snippet(node_text(src, node), max_len=3200)

        relations: list[dict] = []
        for raw_t in type_refs:
            for rt in resolve_type_name(raw_t, package_name, import_map, known_package_types, same_file_types):
                relations.append(self._make_relation(
                    file=rel_path,
                    source_id=ctor_id,
                    source_kind="java_constructor",
                    source_name=f"{class_name}.{name}",
                    relation="uses_type",
                    target=raw_t,
                    target_resolved=rt,
                ))
        for call in calls:
            relations.append(self._make_relation(
                file=rel_path,
                source_id=ctor_id,
                source_kind="java_constructor",
                source_name=f"{class_name}.{name}",
                relation="calls",
                target=call,
                target_resolved=None,
            ))

        return idx, detail, relations


class XmlExtractor:
    def __init__(self, include_xml_node_details: bool = True) -> None:
        self.include_xml_node_details = include_xml_node_details

    def parse(self, rel_path: str, text: str, kind_hint: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        root = etree.fromstring(text.encode("utf-8", errors="ignore"), parser=parser)

        index_records: list[dict] = []
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        namespaces = dict(root.nsmap) if root.nsmap else {}
        root_tag = self._strip_ns(root.tag)

        file_record = {
            "kind": f"{kind_hint}_file",
            "file": rel_path,
            "id": f"{kind_hint}_file:{safe_id(rel_path)}",
            "root": root_tag,
            "namespaces": namespaces,
            "embedding_text": (
                f"{kind_hint.upper()} file {rel_path}. Root tag {root_tag}. "
                f"Namespaces: {', '.join([f'{k}={v}' for k, v in namespaces.items()][:10]) or 'none'}."
            )
        }
        index_records.append(file_record)

        if kind_hint == "xsd":
            idx, det, rel = self._extract_xsd(rel_path, root)
        else:
            idx, det, rel = self._extract_xml(rel_path, root)

        index_records.extend(idx)
        detail_records.extend(det)
        relation_records.extend(rel)

        stats = {
            "kind": kind_hint,
            "file": rel_path,
            "root": root_tag,
            "index_count": len(index_records),
            "detail_count": len(detail_records),
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats

    def _strip_ns(self, tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def _path_of(self, elem) -> str:
        parts = []
        cur = elem
        while cur is not None:
            if isinstance(cur.tag, str):
                parts.append(self._strip_ns(cur.tag))
            cur = cur.getparent()
        return "/" + "/".join(reversed(parts))

    def _make_relation(
        self,
        file: str,
        source_id: str,
        source_kind: str,
        source_name: str,
        relation: str,
        target: str,
        target_resolved: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": "relation",
            "file": file,
            "id": f"relation:{safe_id(file, source_id, relation, target, target_resolved or '')}",
            "source_id": source_id,
            "source_kind": source_kind,
            "source_name": source_name,
            "relation": relation,
            "target": target,
            "target_resolved": target_resolved,
            "weight": 1,
        }

    def _extract_xml(self, rel_path: str, root) -> tuple[list[dict], list[dict], list[dict]]:
        index_records = []
        detail_records = []
        relation_records = []
        tag_first_seen = {}
        tag_attrs = defaultdict(set)
        tag_children = defaultdict(set)

        for elem in root.iter():
            if not isinstance(elem.tag, str):
                continue
            tag = self._strip_ns(elem.tag)
            path = self._path_of(elem)
            attrs = dict(elem.attrib)
            text = (elem.text or "").strip()
            child_tags = [self._strip_ns(c.tag) for c in elem if isinstance(c.tag, str)]
            node_id = f"xml_node:{safe_id(rel_path, path)}"

            if tag not in tag_first_seen:
                tag_first_seen[tag] = path
            tag_attrs[tag].update(attrs.keys())
            tag_children[tag].update(child_tags)

            for child_tag in child_tags:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=node_id,
                    source_kind="xml_node",
                    source_name=path,
                    relation="contains_child_tag",
                    target=child_tag,
                    target_resolved=None,
                ))

            if self.include_xml_node_details:
                detail_records.append({
                    "kind": "xml_node_detail",
                    "file": rel_path,
                    "id": f"xml_node_detail:{safe_id(rel_path, path)}",
                    "tag": tag,
                    "path": path,
                    "attributes": attrs,
                    "text": text[:500],
                    "children": child_tags[:100],
                    "embedding_text": (
                        f"XML node {tag} in file {rel_path}. Path {path}. "
                        f"Attributes: {', '.join(attrs.keys()) or 'none'}. "
                        f"Children: {', '.join(child_tags[:20]) or 'none'}. "
                        f"Text: {text[:200] or 'none'}."
                    )
                })

        for tag, first_path in tag_first_seen.items():
            index_records.append({
                "kind": "xml_tag",
                "file": rel_path,
                "id": f"xml_tag:{safe_id(rel_path, tag)}",
                "tag": tag,
                "first_path": first_path,
                "attribute_names": sorted(tag_attrs[tag]),
                "child_tags": sorted(tag_children[tag]),
                "embedding_text": (
                    f"XML tag {tag} in file {rel_path}. First path {first_path}. "
                    f"Attributes: {', '.join(sorted(tag_attrs[tag])) or 'none'}. "
                    f"Possible children: {', '.join(sorted(tag_children[tag])[:20]) or 'none'}."
                )
            })

        return index_records, detail_records, relation_records

    def _extract_xsd(self, rel_path: str, root) -> tuple[list[dict], list[dict], list[dict]]:
        ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
        index_records = []
        detail_records = []
        relation_records = []

        for elem in root.xpath(".//xs:complexType", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            ct_id = f"xsd_complex_type:{safe_id(rel_path, name)}"

            child_elements = []
            attrs = []
            bases = []

            for e in elem.xpath(".//xs:element", namespaces=ns):
                child_elements.append({
                    "name": e.get("name"),
                    "type": e.get("type"),
                    "ref": e.get("ref"),
                    "minOccurs": e.get("minOccurs"),
                    "maxOccurs": e.get("maxOccurs"),
                })

            for a in elem.xpath(".//xs:attribute", namespaces=ns):
                attrs.append({
                    "name": a.get("name"),
                    "type": a.get("type"),
                    "use": a.get("use"),
                })

            for ex in elem.xpath(".//xs:extension", namespaces=ns):
                if ex.get("base"):
                    bases.append(ex.get("base"))

            index_records.append({
                "kind": "xsd_complex_type",
                "file": rel_path,
                "id": ct_id,
                "name": name,
                "elements": [
                    f"{x.get('name') or x.get('ref')}:{x.get('type')}" for x in child_elements[:50]
                ],
                "attributes": [
                    f"{x.get('name')}:{x.get('type')}" for x in attrs[:50]
                ],
                "extends": bases[:10],
                "embedding_text": (
                    f"XSD complex type {name} in file {rel_path}. "
                    f"Elements: {', '.join([f'{x.get('name') or x.get('ref')}:{x.get('type')}' for x in child_elements[:20]]) or 'none'}. "
                    f"Attributes: {', '.join([f'{x.get('name')}:{x.get('type')}' for x in attrs[:20]]) or 'none'}. "
                    f"Extends: {', '.join(bases[:10]) or 'none'}."
                )
            })

            detail_records.append({
                "kind": "xsd_complex_type_detail",
                "file": rel_path,
                "id": f"xsd_complex_type_detail:{safe_id(rel_path, name)}",
                "name": name,
                "elements": child_elements,
                "attributes": attrs,
                "extends": bases,
            })

            for el in child_elements:
                if el.get("type"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="contains_element_type",
                        target=el.get("name") or el.get("ref") or "",
                        target_resolved=el.get("type"),
                    ))
                elif el.get("ref"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="contains_element_ref",
                        target=el.get("ref"),
                        target_resolved=el.get("ref"),
                    ))

            for at in attrs:
                if at.get("type"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="has_attribute_type",
                        target=at.get("name") or "",
                        target_resolved=at.get("type"),
                    ))

            for base in bases:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=ct_id,
                    source_kind="xsd_complex_type",
                    source_name=name,
                    relation="extends",
                    target=base,
                    target_resolved=base,
                ))

        for elem in root.xpath(".//xs:simpleType", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            restrictions = [r.get("base") for r in elem.xpath(".//xs:restriction", namespaces=ns) if r.get("base")]
            index_records.append({
                "kind": "xsd_simple_type",
                "file": rel_path,
                "id": f"xsd_simple_type:{safe_id(rel_path, name)}",
                "name": name,
                "restrictions": restrictions[:20],
                "embedding_text": (
                    f"XSD simple type {name} in file {rel_path}. "
                    f"Restrictions: {', '.join(restrictions[:20]) or 'none'}."
                )
            })
            for base in restrictions:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=f"xsd_simple_type:{safe_id(rel_path, name)}",
                    source_kind="xsd_simple_type",
                    source_name=name,
                    relation="restricted_by",
                    target=base,
                    target_resolved=base,
                ))

        for elem in root.xpath("./xs:element", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            index_records.append({
                "kind": "xsd_root_element",
                "file": rel_path,
                "id": f"xsd_root_element:{safe_id(rel_path, name)}",
                "name": name,
                "type": elem.get("type"),
                "ref": elem.get("ref"),
                "embedding_text": (
                    f"XSD root element {name} in file {rel_path}. "
                    f"Type {elem.get('type') or 'none'}. Ref {elem.get('ref') or 'none'}."
                )
            })

        return index_records, detail_records, relation_records


def build_package_type_index(files: list[Path], root: Path, java_extractor: JavaExtractor) -> dict[str, set[str]]:
    known_package_types: dict[str, set[str]] = defaultdict(set)
    for path in files:
        if path.suffix.lower() != ".java":
            continue
        rel_path = str(path.relative_to(root))
        text = read_text_file(path)
        if text is None:
            continue
        try:
            scan = java_extractor.pre_scan_types(rel_path, text)
            if scan["package"]:
                for tn in scan["type_names"]:
                    known_package_types[scan["package"]].add(tn)
        except Exception:
            pass
    return dict(known_package_types)


def process_project(
    root: Path,
    out_dir: Path,
    extensions: set[str],
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
) -> None:
    ensure_dir(out_dir)

    java_extractor = JavaExtractor(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
    )
    xml_extractor = XmlExtractor(include_xml_node_details=include_xml_node_details)

    files = collect_files(root, extensions)
    known_package_types = build_package_type_index(files, root, java_extractor)

    all_index: list[dict] = []
    all_details: list[dict] = []
    all_relations: list[dict] = []
    manifest_files: list[dict] = []

    for path in files:
        rel_path = str(path.relative_to(root))
        ext = path.suffix.lower().lstrip(".")
        text = read_text_file(path)
        if text is None:
            manifest_files.append({"file": rel_path, "ext": ext, "error": "unreadable"})
            continue

        try:
            if ext == "java":
                idx, det, rel, stats = java_extractor.parse(
                    rel_path=rel_path,
                    text=text,
                    known_package_types=known_package_types,
                )
            elif ext == "xml":
                idx, det, rel, stats = xml_extractor.parse(rel_path, text, "xml")
            elif ext == "xsd":
                idx, det, rel, stats = xml_extractor.parse(rel_path, text, "xsd")
            else:
                continue

            all_index.extend(idx)
            all_details.extend(det)
            all_relations.extend(rel)

            manifest_files.append({
                "file": rel_path,
                "ext": ext,
                "sha1": sha1_text(text),
                "size": len(text.encode("utf-8", errors="ignore")),
                "stats": stats,
            })
        except Exception as e:
            manifest_files.append({
                "file": rel_path,
                "ext": ext,
                "error": str(e),
            })

    manifest = {
        "project_root": str(root),
        "file_count": len(manifest_files),
        "index_record_count": len(all_index),
        "detail_record_count": len(all_details),
        "relation_record_count": len(all_relations),
        "options": {
            "include_code_snippets": include_code_snippets,
            "exclude_trivial_methods": exclude_trivial_methods,
            "include_xml_node_details": include_xml_node_details,
        },
        "package_type_index": {k: sorted(v) for k, v in known_package_types.items()},
        "files": manifest_files,
    }

    write_jsonl(out_dir / "index.jsonl", all_index)
    write_jsonl(out_dir / "details.jsonl", all_details)
    write_jsonl(out_dir / "relations.jsonl", all_relations)
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Fertig: {out_dir}")
    print(f"Dateien: {len(manifest_files)}")
    print(f"Index Records: {len(all_index)}")
    print(f"Detail Records: {len(all_details)}")
    print(f"Relation Records: {len(all_relations)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Java/XML/XSD project files into AST/structure-based RAG JSONL v3."
    )
    parser.add_argument("root", help="Projektverzeichnis")
    parser.add_argument("-o", "--out", default="rag_out", help="Ausgabeverzeichnis")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=sorted(DEFAULT_EXTENSIONS),
        help="Dateiendungen ohne Punkt"
    )
    parser.add_argument(
        "--exclude-trivial-methods",
        action="store_true",
        help="Getter/Setter und ähnliche triviale Methoden auslassen"
    )
    parser.add_argument(
        "--no-code-snippets",
        action="store_true",
        help="Keine Code-Snippets in details.jsonl"
    )
    parser.add_argument(
        "--no-xml-node-details",
        action="store_true",
        help="Keine detaillierten XML-Node-Records erzeugen"
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    extensions = {x.lower().lstrip(".") for x in args.extensions}

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Ungültiges Verzeichnis: {root}")

    process_project(
        root=root,
        out_dir=out_dir,
        extensions=extensions,
        include_code_snippets=not args.no_code_snippets,
        exclude_trivial_methods=args.exclude_trivial_methods,
        include_xml_node_details=not args.no_xml_node_details,
    )


if __name__ == "__main__":
    main()

