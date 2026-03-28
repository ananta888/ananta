from __future__ import annotations

import unittest

from rag_helper.extractors.java_member_extractor import parse_parameter_bindings, resolve_call_targets


class JavaMethodTargetResolutionTests(unittest.TestCase):
    def test_parse_parameter_bindings_extracts_name_and_type(self) -> None:
        bindings = parse_parameter_bindings(["@Valid UserRepository repo", "OrderService service"])

        self.assertEqual(bindings["repo"], "UserRepository")
        self.assertEqual(bindings["service"], "OrderService")

    def test_resolve_call_targets_prefers_field_type(self) -> None:
        targets = resolve_call_targets(
            calls=["repo.save(user)", "helper.run()"],
            class_name="UserService",
            package_name="demo",
            field_type_lookup={"repo": ["demo.UserRepository"]},
            parameter_bindings={},
            same_file_types={"UserService"},
            resolve_enabled=True,
        )

        self.assertTrue(any(
            target["target_resolved"] == "demo.UserRepository.save"
            and target["heuristic"] == "field_type"
            for target in targets
        ))

    def test_resolve_call_targets_falls_back_to_same_class(self) -> None:
        targets = resolve_call_targets(
            calls=["validate(user)"],
            class_name="UserService",
            package_name="demo",
            field_type_lookup={},
            parameter_bindings={},
            same_file_types={"UserService"},
            resolve_enabled=True,
        )

        self.assertEqual(targets[0]["target_resolved"], "demo.UserService.validate")
        self.assertEqual(targets[0]["heuristic"], "unqualified_same_class")


if __name__ == "__main__":
    unittest.main()
