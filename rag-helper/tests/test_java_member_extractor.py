from __future__ import annotations

import unittest

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java as tsjava
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Language = None
    Parser = None
    tsjava = None

from rag_helper.extractors.java_member_extractor import JavaMemberContext, extract_method


def _parse_first_method(code: str):
    parser = Parser()
    parser.language = Language(tsjava.language())
    src = code.encode("utf-8")
    root = parser.parse(src).root_node

    class_node = None
    for child in root.children:
        if child.type == "class_declaration":
            class_node = child
            break
    assert class_node is not None

    body = next(child for child in class_node.children if child.type == "class_body")
    method_node = next(child for child in body.children if child.type == "method_declaration")
    return src, method_node


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class JavaMemberExtractorTests(unittest.TestCase):
    def test_extract_method_marks_getter_and_return_relation(self) -> None:
        src, method_node = _parse_first_method(
            """
            package demo;
            class User {
                private String name;
                public String getName() { return name; }
            }
            """
        )
        ctx = JavaMemberContext(
            rel_path="User.java",
            src=src,
            package_name="demo",
            import_map={},
            known_package_types={},
            same_file_types={"User"},
            include_code_snippets=True,
        )

        idx, detail, relations, meta = extract_method(ctx, "User", method_node)

        self.assertEqual(idx["name"], "getName")
        self.assertTrue(idx["is_getter"])
        self.assertTrue(idx["is_trivial"])
        self.assertTrue(meta["is_trivial"])
        self.assertIn("code_snippet", detail)
        self.assertTrue(any(
            r["relation"] == "returns" and r["target_resolved"] == "java.lang.String"
            for r in relations
        ))


if __name__ == "__main__":
    unittest.main()
