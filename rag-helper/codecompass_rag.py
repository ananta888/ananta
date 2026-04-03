#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava
try:
    import tree_sitter_c_sharp as tscsharp
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    tscsharp = None

from rag_helper.cli import run_cli
from rag_helper.extractors.adoc_extractor import AdocExtractor
from rag_helper.extractors.csharp_ast_helpers import (
    extract_namespace as extract_csharp_namespace,
    extract_usings,
)
from rag_helper.extractors.csharp_type_extractor import CSharpTypeContext, extract_type as extract_csharp_type
from rag_helper.extractors.csharp_type_resolution import parse_using_map, parse_using_namespaces
from rag_helper.extractors.java_ast_helpers import (
    extract_identifier,
    extract_imports,
    extract_package,
)
from rag_helper.extractors.java_type_extractor import JavaTypeContext, extract_type
from rag_helper.extractors.java_type_resolution import parse_import_map, parse_wildcard_imports
from rag_helper.extractors.text_file_extractor import TextFileExtractor
from rag_helper.extractors.xml_extractor import XmlExtractor
from rag_helper.extractors.xsd_extractor import XsdExtractor
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


DEFAULT_EXTENSIONS = {
    "java", "cs", "xml", "xsd", "adoc",
    "properties", "yaml", "yml", "sql", "md", "py", "ts", "tsx",
}
DEFAULT_EXCLUDES = {
    ".git", ".idea", ".vscode", "target", "build", "dist", "out",
    "node_modules", ".gradle", ".settings", ".mvn", ".venv", "venv",
}


class JavaExtractor:
    def __init__(
        self,
        include_code_snippets: bool = True,
        exclude_trivial_methods: bool = False,
        max_methods_per_class: int | None = None,
        detail_mode: str = "full",
        relation_mode: str = "full",
        resolve_wildcard_imports: bool = True,
        mark_import_conflicts: bool = True,
        resolve_method_targets: bool = True,
        resolve_framework_relations: bool = True,
        embedding_text_mode: str = "verbose",
    ) -> None:
        self.include_code_snippets = include_code_snippets
        self.exclude_trivial_methods = exclude_trivial_methods
        self.max_methods_per_class = max_methods_per_class
        self.detail_mode = detail_mode
        self.relation_mode = relation_mode
        self.resolve_wildcard_imports = resolve_wildcard_imports
        self.mark_import_conflicts = mark_import_conflicts
        self.resolve_method_targets = resolve_method_targets
        self.resolve_framework_relations = resolve_framework_relations
        self.embedding_text_mode = embedding_text_mode
        self.parser = Parser()
        self.parser.language = Language(tsjava.language())

    def _collect_type_names(self, node, src: bytes) -> list[str]:
        names: list[str] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                ident = extract_identifier(current, src)
                if ident:
                    names.append(ident)
            stack.extend(reversed(current.children))
        return names

    def pre_scan_types(self, rel_path: str, text: str) -> dict[str, Any]:
        src = text.encode("utf-8", errors="ignore")
        tree = self.parser.parse(src)
        root = tree.root_node

        package_name = extract_package(root, src)
        imports = extract_imports(root, src)
        type_names = self._collect_type_names(root, src)

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

        package_name = extract_package(root, src)
        imports = extract_imports(root, src)
        import_map = parse_import_map(imports)
        wildcard_imports = parse_wildcard_imports(imports) if self.resolve_wildcard_imports else []

        same_file_types: set[str] = set(self._collect_type_names(root, src))

        type_records = []
        type_names = []
        method_count = 0
        constructor_count = 0
        type_ctx = JavaTypeContext(
            rel_path=rel_path,
            src=src,
            package_name=package_name,
            imports=imports,
            import_map=import_map,
            wildcard_imports=wildcard_imports,
            known_package_types=known_package_types,
            same_file_types=same_file_types,
            include_code_snippets=self.include_code_snippets,
            exclude_trivial_methods=self.exclude_trivial_methods,
            max_methods_per_class=self.max_methods_per_class,
            detail_mode=self.detail_mode,
            relation_mode=self.relation_mode,
            mark_import_conflicts=self.mark_import_conflicts,
            resolve_method_targets=self.resolve_method_targets,
            resolve_framework_relations=self.resolve_framework_relations,
            embedding_text_mode=self.embedding_text_mode,
        )

        for child in root.children:
            if child.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                t_index, t_detail, t_relations, t_stats = extract_type(type_ctx, child)
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

        file_embedding = build_embedding_text(
            self.embedding_text_mode,
            (
            f"Java file {rel_path}. "
            f"Package {package_name or 'default'}. "
            f"Contains types: {', '.join(type_names[:20]) or 'none'}. "
            f"Imports count {len(imports)}. Method count {method_count}. "
            f"Constructor count {constructor_count}."
            ),
            (
                f"Java file {rel_path}. "
                f"Package {package_name or 'default'}. "
                f"Types {compact_list(type_names, limit=6)}. "
                f"Methods {method_count}. Constructors {constructor_count}."
            ),
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


class CSharpExtractor:
    def __init__(
        self,
        include_code_snippets: bool = True,
        exclude_trivial_methods: bool = False,
        max_methods_per_class: int | None = None,
        detail_mode: str = "full",
        relation_mode: str = "full",
        mark_import_conflicts: bool = True,
        resolve_method_targets: bool = True,
        embedding_text_mode: str = "verbose",
    ) -> None:
        if tscsharp is None:
            raise ModuleNotFoundError(
                "tree_sitter_c_sharp fehlt. Bitte installiere die Abhängigkeiten aus requirements.txt."
            )
        self.include_code_snippets = include_code_snippets
        self.exclude_trivial_methods = exclude_trivial_methods
        self.max_methods_per_class = max_methods_per_class
        self.detail_mode = detail_mode
        self.relation_mode = relation_mode
        self.mark_import_conflicts = mark_import_conflicts
        self.resolve_method_targets = resolve_method_targets
        self.embedding_text_mode = embedding_text_mode
        self.parser = Parser()
        self.parser.language = Language(tscsharp.language())

    def _collect_type_names(self, node, src: bytes) -> list[str]:
        names: list[str] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type in {
                "class_declaration",
                "struct_declaration",
                "enum_declaration",
                "interface_declaration",
                "record_declaration",
            }:
                ident = extract_identifier(current, src)
                if ident:
                    names.append(ident)
            stack.extend(reversed(current.children))
        return names

    def pre_scan_types(self, rel_path: str, text: str) -> dict[str, Any]:
        src = text.encode("utf-8", errors="ignore")
        tree = self.parser.parse(src)
        root = tree.root_node
        namespace_name = extract_csharp_namespace(root, src)
        usings = extract_usings(root, src)
        type_names = self._collect_type_names(root, src)
        return {
            "file": rel_path,
            "namespace": namespace_name,
            "usings": usings,
            "type_names": type_names,
        }

    def parse(
        self,
        rel_path: str,
        text: str,
        known_namespace_types: dict[str, set[str]],
    ) -> tuple[list[dict], list[dict], list[dict], dict]:
        src = text.encode("utf-8", errors="ignore")
        tree = self.parser.parse(src)
        root = tree.root_node

        index_records: list[dict] = []
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        namespace_name = extract_csharp_namespace(root, src)
        usings = extract_usings(root, src)
        using_map = parse_using_map(usings)
        using_namespaces = parse_using_namespaces(usings)

        same_file_types: set[str] = set(self._collect_type_names(root, src))

        type_records = []
        type_names = []
        method_count = 0
        constructor_count = 0
        property_count = 0
        type_ctx = CSharpTypeContext(
            rel_path=rel_path,
            src=src,
            namespace_name=namespace_name,
            usings=usings,
            using_map=using_map,
            using_namespaces=using_namespaces,
            known_namespace_types=known_namespace_types,
            same_file_types=same_file_types,
            include_code_snippets=self.include_code_snippets,
            exclude_trivial_methods=self.exclude_trivial_methods,
            max_methods_per_class=self.max_methods_per_class,
            detail_mode=self.detail_mode,
            relation_mode=self.relation_mode,
            mark_import_conflicts=self.mark_import_conflicts,
            resolve_method_targets=self.resolve_method_targets,
            embedding_text_mode=self.embedding_text_mode,
        )

        for child in root.children:
            if child.type in {
                "class_declaration",
                "struct_declaration",
                "enum_declaration",
                "interface_declaration",
                "record_declaration",
            }:
                t_index, t_detail, t_relations, t_stats = extract_csharp_type(type_ctx, child)
                index_records.append(t_index)
                detail_records.extend(t_detail)
                relation_records.extend(t_relations)

                type_records.append({
                    "name": t_index["name"],
                    "type_kind": t_index["type_kind"],
                    "property_count": t_stats["property_count"],
                    "method_count": t_stats["method_count"],
                    "constructor_count": t_stats["constructor_count"],
                    "field_count": t_stats["field_count"],
                    "role_labels": t_index["role_labels"],
                })
                type_names.append(t_index["name"])
                property_count += t_stats["property_count"]
                method_count += t_stats["method_count"]
                constructor_count += t_stats["constructor_count"]

        file_embedding = build_embedding_text(
            self.embedding_text_mode,
            (
                f"CSharp file {rel_path}. "
                f"Namespace {namespace_name or 'global'}. "
                f"Contains types: {', '.join(type_names[:20]) or 'none'}. "
                f"Using count {len(usings)}. Property count {property_count}. "
                f"Method count {method_count}. Constructor count {constructor_count}."
            ),
            (
                f"CSharp file {rel_path}. "
                f"Namespace {namespace_name or 'global'}. "
                f"Types {compact_list(type_names, limit=6)}. "
                f"Properties {property_count}. Methods {method_count}."
            ),
        )

        file_record = {
            "kind": "cs_file",
            "file": rel_path,
            "id": f"cs_file:{safe_id(rel_path)}",
            "namespace": namespace_name,
            "usings": usings,
            "types": type_records,
            "embedding_text": file_embedding,
            "summary": {
                "using_count": len(usings),
                "type_count": len(type_records),
                "property_count": property_count,
                "method_count": method_count,
                "constructor_count": constructor_count,
            },
        }
        index_records.insert(0, file_record)

        stats = {
            "kind": "cs",
            "file": rel_path,
            "namespace": namespace_name,
            "using_count": len(usings),
            "type_count": len(type_records),
            "property_count": property_count,
            "method_count": method_count,
            "constructor_count": constructor_count,
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats


def main() -> None:
    run_cli(
        default_extensions=DEFAULT_EXTENSIONS,
        default_excludes=DEFAULT_EXCLUDES,
        java_extractor_cls=JavaExtractor,
        csharp_extractor_cls=CSharpExtractor,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
        text_extractor_cls=TextFileExtractor,
    )


if __name__ == "__main__":
    main()
