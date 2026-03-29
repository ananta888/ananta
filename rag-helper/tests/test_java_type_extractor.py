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
            wildcard_imports=[],
            known_package_types={},
            same_file_types={"User"},
            include_code_snippets=False,
            exclude_trivial_methods=True,
            max_methods_per_class=None,
            detail_mode="full",
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            resolve_framework_relations=True,
            embedding_text_mode="verbose",
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

    def test_extract_type_respects_max_methods_per_class(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            class User {
                public String one() { return "1"; }
                public String two() { return "2"; }
            }
            """
        )
        ctx = JavaTypeContext(
            rel_path="User.java",
            src=src,
            package_name="demo",
            imports=[],
            import_map={},
            wildcard_imports=[],
            known_package_types={},
            same_file_types={"User"},
            include_code_snippets=False,
            exclude_trivial_methods=False,
            max_methods_per_class=1,
            detail_mode="full",
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            resolve_framework_relations=True,
            embedding_text_mode="verbose",
        )

        type_record, detail_records, relation_records, stats = extract_type(ctx, type_node)

        self.assertEqual(type_record["methods"], ["one(): String"])
        self.assertEqual(stats["method_count"], 1)
        self.assertEqual(stats["skipped_method_count"], 1)
        self.assertEqual(sum(1 for rel in relation_records if rel["relation"] == "declares_method"), 1)
        self.assertEqual(sum(1 for record in detail_records if record["kind"] == "java_method"), 1)

    def test_extract_type_marks_resolution_conflicts_from_wildcard_imports(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo.app;
            class User {
                private OrderDto dto;
            }
            """
        )
        ctx = JavaTypeContext(
            rel_path="User.java",
            src=src,
            package_name="demo.app",
            imports=["demo.shared.*"],
            import_map={},
            wildcard_imports=["demo.shared"],
            known_package_types={
                "demo.app": {"User", "OrderDto"},
                "demo.shared": {"OrderDto"},
            },
            same_file_types={"User"},
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

        type_record, _, relation_records, _ = extract_type(ctx, type_node)

        self.assertEqual(
            type_record["fields"][0]["resolved_types"],
            ["demo.app.OrderDto", "demo.shared.OrderDto"],
        )
        self.assertTrue(type_record["type_resolution_conflicts"])
        self.assertTrue(any(rel["relation"] == "field_type_uses" for rel in relation_records))

    def test_extract_type_adds_probable_call_targets_from_field_types(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            class UserService {
                private UserRepository repo;

                public void save(User user) {
                    repo.save(user);
                    validate(user);
                }
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

        _, detail_records, relation_records, _ = extract_type(ctx, type_node)

        method_record = next(record for record in detail_records if record["kind"] == "java_method")
        self.assertTrue(any(
            target["target_resolved"] == "demo.UserRepository.save"
            for target in method_record["resolved_call_targets"]
        ))
        self.assertTrue(any(
            rel["relation"] == "calls_probable_target"
            and rel["target_resolved"] == "demo.UserRepository.save"
            for rel in relation_records
        ))

    def test_extract_type_adds_spring_and_jpa_relations(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            @Configuration
            @Transactional
            class UserConfig {
                @Autowired
                private UserRepository repo;

                @OneToMany
                @JoinColumn(name = "user_id")
                private java.util.List<Order> orders;

                @Bean
                public UserService userService() { return new UserService(); }
            }
            """
        )
        ctx = JavaTypeContext(
            rel_path="UserConfig.java",
            src=src,
            package_name="demo",
            imports=[],
            import_map={},
            wildcard_imports=[],
            known_package_types={"demo": {"UserConfig", "UserRepository", "UserService", "Order"}},
            same_file_types={"UserConfig"},
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

        _, _, relation_records, _ = extract_type(ctx, type_node)

        relation_names = {rel["relation"] for rel in relation_records}
        self.assertIn("spring_configuration", relation_names)
        self.assertIn("transactional_boundary", relation_names)
        self.assertIn("injects_dependency", relation_names)
        self.assertIn("jpa_one_to_many", relation_names)
        self.assertIn("jpa_join_column", relation_names)
        self.assertIn("declares_bean", relation_names)

    def test_extract_type_compact_detail_mode_omits_redundant_detail_records(self) -> None:
        src, type_node = _parse_first_type(
            """
            package demo;
            class UserService {
                UserService() {}
                public void save() {}
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
            detail_mode="compact",
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            resolve_framework_relations=True,
            embedding_text_mode="verbose",
        )

        _, detail_records, _, _ = extract_type(ctx, type_node)

        kinds = {record["kind"] for record in detail_records}
        self.assertIn("java_method", kinds)
        self.assertIn("java_constructor", kinds)
        self.assertNotIn("java_method_detail", kinds)
        self.assertNotIn("java_constructor_detail", kinds)


if __name__ == "__main__":
    unittest.main()
