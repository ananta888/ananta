from __future__ import annotations

import unittest

from rag_helper.extractors.java_type_resolution import (
    find_resolution_conflicts,
    parse_import_map,
    parse_wildcard_imports,
    resolve_type_name,
)


class JavaTypeResolutionTests(unittest.TestCase):
    def test_parse_wildcard_imports_keeps_non_static_packages(self) -> None:
        wildcard_imports = parse_wildcard_imports([
            "java.util.*",
            "static:java.util.Collections.*",
            "demo.shared.*",
        ])

        self.assertEqual(wildcard_imports, ["java.util", "demo.shared"])

    def test_resolve_type_name_uses_wildcard_import_candidates(self) -> None:
        resolved = resolve_type_name(
            type_text="OrderDto",
            package_name="demo.app",
            import_map=parse_import_map([]),
            known_package_types={
                "demo.shared": {"OrderDto"},
                "demo.other": {"OrderDto"},
            },
            same_file_types=set(),
            wildcard_imports=["demo.shared"],
        )

        self.assertEqual(resolved, ["demo.shared.OrderDto"])

    def test_resolve_type_name_marks_conflicts_for_same_short_name(self) -> None:
        resolved = resolve_type_name(
            type_text="OrderDto",
            package_name="demo.app",
            import_map=parse_import_map([]),
            known_package_types={
                "demo.app": {"OrderDto"},
                "demo.shared": {"OrderDto"},
            },
            same_file_types=set(),
            wildcard_imports=["demo.shared"],
        )

        conflicts = find_resolution_conflicts("OrderDto", resolved)

        self.assertEqual(resolved, ["demo.app.OrderDto", "demo.shared.OrderDto"])
        self.assertEqual(conflicts[0]["type_name"], "OrderDto")
        self.assertEqual(conflicts[0]["candidates"], resolved)

    def test_resolve_type_name_qualifies_inner_type_from_same_file(self) -> None:
        resolved = resolve_type_name(
            type_text="Outer.Inner",
            package_name="demo.app",
            import_map=parse_import_map([]),
            known_package_types={},
            same_file_types={"Outer", "Inner"},
            wildcard_imports=[],
        )

        self.assertEqual(resolved, ["demo.app.Outer.Inner"])


if __name__ == "__main__":
    unittest.main()
