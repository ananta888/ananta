from __future__ import annotations

import unittest

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java as tsjava
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Language = None
    Parser = None
    tsjava = None

from rag_helper.extractors.java_type_extractor import JavaTypeContext, extract_type


def _parse_first_type(code: str):
    parser = Parser()
    parser.language = Language(tsjava.language())
    src = code.encode("utf-8")
    root = parser.parse(src).root_node
    type_node = next(child for child in root.children if child.type == "class_declaration")
    return src, type_node


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class JavaTypeExtractorTests(unittest.TestCase):
    def test_extract_type_excludes_trivial_methods_when_requested(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            class User {
                private String name;

                public String getName() { return name; }

                public String displayName() { return this.name + "!"; }
            }
            """
        )
        ctx = JavaTypeContext(
            rel_path="User.java",
            src=src,
            package_name="demo",
            imports=[],
            import_map={},
            known_package_types={},
            same_file_types={"User"},
            include_code_snippets=False,
            exclude_trivial_methods=True,
        )

        type_record, detail_records, relation_records, stats = extract_type(ctx, type_node)

        self.assertEqual(type_record["name"], "User")
        self.assertEqual(type_record["methods"], ["displayName(): String"])
        self.assertEqual(stats["method_count"], 1)
        self.assertTrue(any(
            record["kind"] == "java_method" and record["name"] == "displayName"
            for record in detail_records
        ))
        self.assertTrue(all(
            not (record["kind"] == "java_method" and record["name"] == "getName")
            for record in detail_records
            if "name" in record
        ))
        self.assertTrue(any(rel["relation"] == "declares_method" for rel in relation_records))


if __name__ == "__main__":
    unittest.main()
