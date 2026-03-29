from __future__ import annotations

import unittest

try:
    from rag_helper.extractors.xsd_extractor import XsdExtractor
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    XsdExtractor = None

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
class JavaParentChildLinkTests(unittest.TestCase):
    def test_java_type_method_and_constructor_have_parent_links(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            class UserService {
                UserService() {}
                void save() {}
            }
            """
        )
        ctx = JavaTypeContext(
            rel_path="UserService.java",
            src=src,
            package_name="demo",
            imports=[],
            import_map={},
            wildcard_imports=[],
            known_package_types={"demo": {"UserService"}},
            same_file_types={"UserService"},
            include_code_snippets=False,
            exclude_trivial_methods=False,
            max_methods_per_class=None,
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            resolve_framework_relations=True,
            embedding_text_mode="verbose",
        )

        type_record, detail_records, relation_records, _ = extract_type(ctx, type_node)

        method_record = next(record for record in detail_records if record["kind"] == "java_method")
        constructor_record = next(record for record in detail_records if record["kind"] == "java_constructor")
        self.assertTrue(type_record["parent_id"].startswith("java_file:"))
        self.assertEqual(method_record["parent_id"], type_record["id"])
        self.assertEqual(constructor_record["parent_id"], type_record["id"])
        self.assertTrue(any(rel["relation"] == "contains_type" for rel in relation_records))
        self.assertTrue(any(rel["relation"] == "child_of_type" for rel in relation_records))


@unittest.skipUnless(XsdExtractor is not None, "lxml dependency missing")
class XsdParentChildLinkTests(unittest.TestCase):
    def test_xsd_records_get_parent_links(self) -> None:
        extractor = XsdExtractor()
        index_records, detail_records, relation_records, _ = extractor.parse(
            "schema.xsd",
            """
            <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
              <xs:complexType name="UserType">
                <xs:sequence>
                  <xs:element name="name" type="xs:string"/>
                </xs:sequence>
              </xs:complexType>
              <xs:element name="user" type="UserType"/>
            </xs:schema>
            """,
        )

        file_record = index_records[0]
        complex_type = next(record for record in index_records if record["kind"] == "xsd_complex_type")
        complex_detail = next(record for record in detail_records if record["kind"] == "xsd_complex_type_detail")
        root_element = next(record for record in index_records if record["kind"] == "xsd_root_element")

        self.assertEqual(complex_type["parent_id"], file_record["id"])
        self.assertEqual(complex_detail["parent_id"], complex_type["id"])
        self.assertEqual(root_element["parent_id"], file_record["id"])
        self.assertTrue(any(rel["relation"] == "contains_complex_type" for rel in relation_records))
        self.assertTrue(any(rel["relation"] == "contains_root_element" for rel in relation_records))


if __name__ == "__main__":
    unittest.main()
