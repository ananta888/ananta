#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

from rag_helper.cli import run_cli
from rag_helper.extractors.java_ast_helpers import (
    extract_identifier,
    extract_imports,
    extract_package,
)
from rag_helper.extractors.java_type_extractor import JavaTypeContext, extract_type
from rag_helper.extractors.java_type_resolution import parse_import_map
from rag_helper.extractors.xml_extractor import XmlExtractor
from rag_helper.extractors.xsd_extractor import XsdExtractor
from rag_helper.utils.ids import safe_id


DEFAULT_EXTENSIONS = {"java", "xml", "xsd"}
DEFAULT_EXCLUDES = {
    ".git", ".idea", ".vscode", "target", "build", "dist", "out",
    "node_modules", ".gradle", ".settings", ".mvn",
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

        package_name = extract_package(root, src)
        imports = extract_imports(root, src)
        type_names: list[str] = []

        for child in root.children:
            if child.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                ident = extract_identifier(child, src)
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

        package_name = extract_package(root, src)
        imports = extract_imports(root, src)
        import_map = parse_import_map(imports)

        same_file_types: set[str] = set()
        for child in root.children:
            if child.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                ident = extract_identifier(child, src)
                if ident:
                    same_file_types.add(ident)

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
            known_package_types=known_package_types,
            same_file_types=same_file_types,
            include_code_snippets=self.include_code_snippets,
            exclude_trivial_methods=self.exclude_trivial_methods,
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


def main() -> None:
    run_cli(
        default_extensions=DEFAULT_EXTENSIONS,
        default_excludes=DEFAULT_EXCLUDES,
        java_extractor_cls=JavaExtractor,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
    )


if __name__ == "__main__":
    main()
