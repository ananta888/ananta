"""CCAQE-013/014/015: typed use edges, test/controller edges and policy edges."""
from __future__ import annotations

import unittest

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java as tsjava
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Language = None
    Parser = None
    tsjava = None

from rag_helper.application.output_formats import build_graph_edges
from rag_helper.extractors.java_type_extractor import JavaTypeContext, extract_type


def _parse_first_type(code: str):
    parser = Parser()
    parser.language = Language(tsjava.language())
    src = code.encode("utf-8")
    root = parser.parse(src).root_node
    type_node = next(child for child in root.children if child.type == "class_declaration")
    return src, type_node


def _make_ctx(src: bytes, rel_path: str, known_types: set[str], same_file: set[str]) -> JavaTypeContext:
    return JavaTypeContext(
        rel_path=rel_path,
        src=src,
        package_name="demo",
        imports=[],
        import_map={},
        wildcard_imports=[],
        known_package_types={"demo": known_types},
        same_file_types=same_file,
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


def _relations_of(relations: list[dict], relation: str) -> list[dict]:
    return [record for record in relations if record.get("relation") == relation]


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class TypedUseEdgeTests(unittest.TestCase):
    """CCAQE-013: field, parameter, return and generic type uses."""

    def _extract(self):
        src, node = _parse_first_type(
            """
            package demo;
            class UserService {
                private UserDto dto;
                private List<UserDto> items;

                public void save(UserDto incoming) { }

                public UserDto getUser() { return dto; }
            }
            """
        )
        ctx = _make_ctx(src, "UserService.java", {"UserService", "UserDto"}, {"UserService"})
        _, _, relations, _ = extract_type(ctx, node)
        return relations

    def test_field_creates_field_type_uses(self) -> None:
        relations = self._extract()
        hits = _relations_of(relations, "field_type_uses")
        self.assertTrue(any(record["target"] == "UserDto" for record in hits))

    def test_method_parameter_creates_method_param_type_uses(self) -> None:
        relations = self._extract()
        hits = _relations_of(relations, "method_param_type_uses")
        self.assertTrue(any(record["target"] == "UserDto" for record in hits))

    def test_method_return_creates_method_return_type_uses(self) -> None:
        relations = self._extract()
        hits = _relations_of(relations, "method_return_type_uses")
        self.assertTrue(any(record["target"] == "UserDto" for record in hits))

    def test_generic_argument_creates_generic_type_uses(self) -> None:
        relations = self._extract()
        hits = _relations_of(relations, "generic_type_uses")
        self.assertTrue(any(record["target"] == "UserDto" for record in hits))


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class TestAndControllerEdgeTests(unittest.TestCase):
    """CCAQE-014: test_targets_type, controller_endpoint_declares, test_calls_endpoint."""

    def _extract_controller(self):
        src, node = _parse_first_type(
            """
            package demo;
            @RestController
            class UserController {
                @GetMapping("/users")
                public List<UserDto> getUsers() { return null; }
            }
            """
        )
        ctx = _make_ctx(src, "UserController.java", {"UserController", "UserDto"}, {"UserController"})
        _, _, relations, _ = extract_type(ctx, node)
        return relations

    def _extract_test(self):
        src, node = _parse_first_type(
            """
            package demo;
            @WebMvcTest(UserController.class)
            class UserControllerTest {
                @MockBean
                private UserService service;

                private UserController controller;

                public void shouldListUsers() throws Exception {
                    mockMvc.perform(get("/users")).andExpect(status().isOk());
                }
            }
            """
        )
        ctx = _make_ctx(
            src,
            "src/test/java/demo/UserControllerTest.java",
            {"UserControllerTest", "UserController", "UserService"},
            {"UserControllerTest"},
        )
        _, _, relations, _ = extract_type(ctx, node)
        return relations

    def test_webmvctest_creates_test_targets_type(self) -> None:
        relations = self._extract_test()
        hits = _relations_of(relations, "test_targets_type")
        self.assertTrue(any(record["target"] == "UserController" for record in hits))

    def test_mapping_annotation_creates_controller_endpoint_declares(self) -> None:
        relations = self._extract_controller()
        hits = _relations_of(relations, "controller_endpoint_declares")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["endpoint_path"], "/users")
        self.assertEqual(hits[0]["http_method"], "GET")

    def test_mockmvc_perform_creates_test_calls_endpoint_with_reduced_confidence(self) -> None:
        relations = self._extract_test()
        hits = _relations_of(relations, "test_calls_endpoint")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["target"], "/users")
        self.assertLess(hits[0]["confidence"], 0.95)
        self.assertEqual(hits[0]["heuristic"], "mockmvc_string_match")

    def test_controller_field_in_test_creates_test_uses_controller(self) -> None:
        relations = self._extract_test()
        hits = _relations_of(relations, "test_uses_controller")
        self.assertTrue(any(record["target"] == "UserController" for record in hits))

    def test_mockbean_field_creates_mock_injects_dependency(self) -> None:
        relations = self._extract_test()
        hits = _relations_of(relations, "mock_injects_dependency")
        self.assertTrue(any(record["target"] == "UserService" for record in hits))

    def test_non_test_class_gets_no_test_edges(self) -> None:
        relations = self._extract_controller()
        self.assertEqual(_relations_of(relations, "test_targets_type"), [])
        self.assertEqual(_relations_of(relations, "test_uses_controller"), [])


@unittest.skipUnless(Language is not None and Parser is not None and tsjava is not None, "tree_sitter dependencies missing")
class PolicyEdgeTests(unittest.TestCase):
    """CCAQE-015: permission_checks_field, role_allows_operation, interceptor_guards_method."""

    def _extract_service(self):
        src, node = _parse_first_type(
            """
            package demo;
            class PriceService {
                @PreAuthorize("hasPermission(#dto, 'price.update')")
                public void updatePrice(UserDto dto) { }

                @Secured("ROLE_ADMIN")
                public void deleteUser(String id) { }

                @PriceFieldGuard
                public void changeLimit() { }
            }
            """
        )
        ctx = _make_ctx(src, "PriceService.java", {"PriceService", "UserDto", "PriceFieldGuard"}, {"PriceService"})
        _, _, relations, _ = extract_type(ctx, node)
        return relations

    def test_pre_authorize_field_token_creates_permission_checks_field(self) -> None:
        relations = self._extract_service()
        hits = _relations_of(relations, "permission_checks_field")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["field"], "price")
        self.assertEqual(hits[0]["operation"], "update")
        self.assertEqual(hits[0]["target_resolved"], "demo.UserDto")
        self.assertTrue(hits[0]["source_file"])

    def test_secured_creates_role_allows_operation_with_inferred_operation(self) -> None:
        relations = self._extract_service()
        hits = _relations_of(relations, "role_allows_operation")
        self.assertTrue(any(record["target"] == "ROLE_ADMIN" and record["operation"] == "delete" for record in hits))

    def test_custom_guard_annotation_creates_interceptor_guards_method(self) -> None:
        relations = self._extract_service()
        hits = _relations_of(relations, "interceptor_guards_method")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source_id"], "demo.PriceFieldGuard")
        self.assertEqual(hits[0]["heuristic"], "annotation_guard")
        self.assertTrue(hits[0]["target_resolved"].startswith("java_method:"))


class GraphEdgeResolutionTests(unittest.TestCase):
    """CCAQE-013/014: build_graph_edges resolves symbol relations to node ids."""

    _INDEX = [
        {"id": "java_type:UserDto", "kind": "java_type", "name": "UserDto", "package": "demo", "file": "UserDto.java"},
        {"id": "java_type:UserService", "kind": "java_type", "name": "UserService", "package": "demo", "file": "UserService.java"},
        {"id": "java_method:UserController.getUsers", "kind": "java_method", "name": "getUsers", "file": "UserController.java"},
        {"id": "java_type:UserControllerTest", "kind": "java_type", "name": "UserControllerTest", "package": "demo", "file": "UserControllerTest.java"},
    ]

    def test_relation_with_fqn_target_becomes_graph_edge(self) -> None:
        edges = build_graph_edges(
            index_records=self._INDEX,
            detail_records=[],
            relation_records=[{
                "kind": "relation",
                "source_id": "java_type:UserService",
                "relation": "field_type_uses",
                "target": "UserDto",
                "target_resolved": "demo.UserDto",
                "confidence": 0.95,
            }],
        )
        typed = [edge for edge in edges if edge["type"] == "field_type_uses"]
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0]["source"], "java_type:UserService")
        self.assertEqual(typed[0]["target"], "java_type:UserDto")
        self.assertEqual(typed[0]["confidence"], 0.95)

    def test_unresolvable_relation_target_is_dropped(self) -> None:
        edges = build_graph_edges(
            index_records=self._INDEX,
            detail_records=[],
            relation_records=[{
                "kind": "relation",
                "source_id": "java_type:UserService",
                "relation": "field_type_uses",
                "target": "ExternalThing",
                "target_resolved": "org.external.ExternalThing",
            }],
        )
        self.assertEqual([edge for edge in edges if edge["type"] == "field_type_uses"], [])

    def test_test_calls_endpoint_resolves_via_declared_endpoint_path(self) -> None:
        edges = build_graph_edges(
            index_records=self._INDEX,
            detail_records=[],
            relation_records=[
                {
                    "kind": "relation",
                    "source_id": "java_type:UserController",
                    "relation": "controller_endpoint_declares",
                    "target": "getUsers",
                    "target_resolved": "java_method:UserController.getUsers",
                    "endpoint_path": "/users",
                    "http_method": "GET",
                },
                {
                    "kind": "relation",
                    "source_id": "java_type:UserControllerTest",
                    "relation": "test_calls_endpoint",
                    "target": "/users",
                    "target_resolved": None,
                    "confidence": 0.6,
                    "heuristic": "mockmvc_string_match",
                },
            ],
        )
        endpoint_edges = [edge for edge in edges if edge["type"] == "test_calls_endpoint"]
        self.assertEqual(len(endpoint_edges), 1)
        self.assertEqual(endpoint_edges[0]["source"], "java_type:UserControllerTest")
        self.assertEqual(endpoint_edges[0]["target"], "java_method:UserController.getUsers")

    def test_policy_edge_keeps_field_and_operation_attributes(self) -> None:
        edges = build_graph_edges(
            index_records=self._INDEX,
            detail_records=[],
            relation_records=[{
                "kind": "relation",
                "source_id": "java_type:UserService",
                "relation": "permission_checks_field",
                "target": "price",
                "target_resolved": "demo.UserDto",
                "confidence": 0.85,
                "field": "price",
                "operation": "update",
            }],
        )
        policy_edges = [edge for edge in edges if edge["type"] == "permission_checks_field"]
        self.assertEqual(len(policy_edges), 1)
        self.assertEqual(policy_edges[0]["field"], "price")
        self.assertEqual(policy_edges[0]["operation"], "update")

    def test_legacy_from_to_relations_still_become_edges(self) -> None:
        edges = build_graph_edges(
            index_records=self._INDEX,
            detail_records=[],
            relation_records=[{"from": "java_type:UserService", "to": "java_type:UserDto", "type": "contains_section"}],
        )
        legacy = [edge for edge in edges if edge["type"] == "contains_section"]
        self.assertEqual(len(legacy), 1)


if __name__ == "__main__":
    unittest.main()
