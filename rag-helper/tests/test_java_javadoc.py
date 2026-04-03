from __future__ import annotations

from types import SimpleNamespace
import unittest

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java as tsjava
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Language = None
    Parser = None
    tsjava = None

from rag_helper.extractors.java_ast_helpers import extract_javadoc, extract_javadoc_summary
from rag_helper.extractors.java_type_extractor import JavaTypeContext, extract_type


class JavaJavadocHelperTests(unittest.TestCase):
    def test_extract_javadoc_normalizes_body_and_tags(self) -> None:
        src = (
            b"/**\n"
            b" * Saves a user.\n"
            b" *\n"
            b" * @param id user id\n"
            b" * @return saved user\n"
            b" */\n"
            b"void save() {}"
        )

        node = SimpleNamespace(start_byte=src.index(b"void"))
        javadoc = extract_javadoc(node, src)

        self.assertEqual(javadoc, "Saves a user.\n\n@param id user id\n@return saved user")
        self.assertEqual(extract_javadoc_summary(javadoc), "Saves a user.")

    def test_extract_javadoc_ignores_non_javadoc_blocks(self) -> None:
        src = b"/* plain block */\nvoid save() {}"
        node = SimpleNamespace(start_byte=src.index(b"void"))

        self.assertIsNone(extract_javadoc(node, src))

    def test_extract_javadoc_requires_direct_adjacency(self) -> None:
        src = b"/** Type doc. */\nprivate int x;\nvoid save() {}"
        node = SimpleNamespace(start_byte=src.index(b"void"))

        self.assertIsNone(extract_javadoc(node, src))


def _parse_first_type(code: str):
    parser = Parser()
    parser.language = Language(tsjava.language())
    src = code.encode("utf-8")
    root = parser.parse(src).root_node
    type_node = next(child for child in root.children if child.type == "class_declaration")
    return src, type_node


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class JavaJavadocIntegrationTests(unittest.TestCase):
    def test_extract_type_and_members_include_javadoc(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            /** Service for user writes.
             * Handles create flows.
             */
            class UserService {
                /** Repository field. */
                private UserRepository repo;

                /** Creates the service. */
                UserService() {}

                /** Saves a user.
                 * @param user input value
                 * @return saved user
                 */
                public User save(User user) { return user; }
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
            known_package_types={"demo": {"UserService", "UserRepository", "User"}},
            same_file_types={"UserService"},
            include_code_snippets=False,
            exclude_trivial_methods=False,
            max_methods_per_class=None,
            detail_mode="full",
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            resolve_framework_relations=True,
            embedding_text_mode="verbose",
        )

        type_record, detail_records, _, _ = extract_type(ctx, type_node)

        method_record = next(record for record in detail_records if record["kind"] == "java_method")
        constructor_record = next(record for record in detail_records if record["kind"] == "java_constructor")

        self.assertEqual(type_record["javadoc_summary"], "Service for user writes.")
        self.assertIn("Handles create flows.", type_record["javadoc"])
        self.assertEqual(type_record["fields"][0]["javadoc_summary"], "Repository field.")
        self.assertEqual(constructor_record["javadoc_summary"], "Creates the service.")
        self.assertEqual(method_record["javadoc_summary"], "Saves a user.")
        self.assertIn("@param user input value", method_record["javadoc"])
        self.assertIn("Javadoc: Saves a user.", method_record["embedding_text"])


if __name__ == "__main__":
    unittest.main()
