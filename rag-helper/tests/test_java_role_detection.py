from __future__ import annotations

import unittest

from rag_helper.extractors.java_role_detection import detect_type_roles


class JavaRoleDetectionTests(unittest.TestCase):
    def test_detects_configuration_mapper_client_and_exception_roles(self) -> None:
        roles = detect_type_roles(
            type_name="UserConfig",
            type_kind="class",
            annotations=["@Configuration", "@FeignClient(name = \"users\")"],
            imports=["org.mapstruct.Mapper", "lombok.Data"],
            fields=[],
            methods=[],
        )

        self.assertIn("config", roles["role_labels"])
        self.assertIn("client", roles["role_labels"])

        mapper_roles = detect_type_roles(
            type_name="UserMapper",
            type_kind="interface",
            annotations=["@Mapper"],
            imports=[],
            fields=[],
            methods=[],
        )
        self.assertIn("mapper", mapper_roles["role_labels"])

        exception_roles = detect_type_roles(
            type_name="OrderException",
            type_kind="class",
            annotations=[],
            imports=[],
            fields=[],
            methods=[],
        )
        self.assertIn("exception", exception_roles["role_labels"])

    def test_detects_util_adapter_facade_and_enum_model_roles(self) -> None:
        util_roles = detect_type_roles(
            type_name="DateUtils",
            type_kind="class",
            annotations=[],
            imports=[],
            fields=[],
            methods=[{"modifiers": ["public", "static"]}, {"modifiers": ["private", "static"]}],
        )
        self.assertIn("util", util_roles["role_labels"])

        adapter_roles = detect_type_roles(
            type_name="BillingAdapter",
            type_kind="class",
            annotations=[],
            imports=[],
            fields=[],
            methods=[],
        )
        self.assertIn("adapter", adapter_roles["role_labels"])

        facade_roles = detect_type_roles(
            type_name="CheckoutFacade",
            type_kind="class",
            annotations=[],
            imports=[],
            fields=[],
            methods=[],
        )
        self.assertIn("facade", facade_roles["role_labels"])

        enum_roles = detect_type_roles(
            type_name="OrderStatus",
            type_kind="enum",
            annotations=[],
            imports=[],
            fields=[],
            methods=[],
        )
        self.assertIn("enum_model", enum_roles["role_labels"])


if __name__ == "__main__":
    unittest.main()
