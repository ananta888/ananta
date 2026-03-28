from __future__ import annotations

import unittest

from rag_helper.extractors.java_type_extractor import (
    build_field_framework_relations,
    build_method_framework_relations,
    build_type_framework_relations,
)


class FrameworkRelationTests(unittest.TestCase):
    def test_build_field_framework_relations_handles_injection_and_jpa(self) -> None:
        relations = build_field_framework_relations(
            rel_path="User.java",
            type_id="java_type:1",
            type_name="User",
            field={
                "type": "Order",
                "annotations": ["@Autowired", "@ManyToOne", "@JoinColumn(name=\"order_id\")"],
                "declarators": ["order"],
                "resolved_types": ["demo.Order"],
            },
        )

        relation_names = {relation["relation"] for relation in relations}
        self.assertIn("injects_dependency", relation_names)
        self.assertIn("jpa_many_to_one", relation_names)
        self.assertIn("jpa_join_column", relation_names)

    def test_build_method_framework_relations_marks_bean_and_transactional(self) -> None:
        relations = build_method_framework_relations(
            rel_path="Config.java",
            type_id="java_type:1",
            type_name="Config",
            method_record={
                "id": "java_method:1",
                "name": "service",
                "annotations": ["@Bean", "@Transactional"],
                "resolved_return_types": ["demo.UserService"],
                "return_type": "UserService",
            },
        )

        relation_names = {relation["relation"] for relation in relations}
        self.assertIn("declares_bean", relation_names)
        self.assertIn("bean_factory_method", relation_names)
        self.assertIn("transactional_boundary", relation_names)

    def test_build_type_framework_relations_marks_configuration_and_repository_bases(self) -> None:
        relations = build_type_framework_relations(
            rel_path="Repo.java",
            type_id="java_type:1",
            type_name="Repo",
            annotations=["@Configuration", "@Entity"],
            resolved_extends=["org.springframework.data.jpa.repository.JpaRepository"],
        )

        relation_names = {relation["relation"] for relation in relations}
        self.assertIn("spring_configuration", relation_names)
        self.assertIn("jpa_entity_role", relation_names)
        self.assertIn("repository_extends_framework", relation_names)


if __name__ == "__main__":
    unittest.main()
