from __future__ import annotations

import unittest

from rag_helper.domain_discovery.signals import (
    assign_root_path,
    collect_package_prefixes,
    derive_root_path_candidates,
    package_prefixes_from_manifest,
)


def _mixed_repo_files() -> list[str]:
    files = []
    # rag-helper: wrapper dir with one dominant child package
    files += [f"rag-helper/rag_helper/application/mod{i}.py" for i in range(4)]
    files += ["rag-helper/rag_helper/cli.py", "rag-helper/rag_helper/domain.py"]
    # agent: few direct files, several large children -> split into sub-roots
    files += [f"agent/services/service{i}.py" for i in range(5)]
    files += [f"agent/routes/route{i}.py" for i in range(4)]
    # client_surfaces: one big child below min_files each -> stays whole?
    files += [
        "client_surfaces/tui/app.py",
        "client_surfaces/tui/render.py",
        "client_surfaces/web/index.ts",
        "client_surfaces/vscode/ext.ts",
    ]
    return files


class TestRootPathCandidates(unittest.TestCase):
    def test_dominant_child_descends_into_package_dir(self) -> None:
        candidates = derive_root_path_candidates(_mixed_repo_files())
        roots = [c.root_path for c in candidates]
        self.assertIn("rag-helper/rag_helper", roots)
        self.assertNotIn("rag-helper", roots)

    def test_heterogeneous_parent_splits_into_sub_roots(self) -> None:
        candidates = derive_root_path_candidates(_mixed_repo_files())
        roots = [c.root_path for c in candidates]
        self.assertIn("agent/services", roots)
        self.assertIn("agent/routes", roots)
        self.assertNotIn("agent", roots)

    def test_small_children_keep_parent_as_candidate(self) -> None:
        candidates = derive_root_path_candidates(_mixed_repo_files())
        roots = [c.root_path for c in candidates]
        self.assertIn("client_surfaces", roots)
        self.assertNotIn("client_surfaces/tui", roots)

    def test_candidates_are_reproducible_and_sorted(self) -> None:
        files = _mixed_repo_files()
        first = derive_root_path_candidates(files)
        second = derive_root_path_candidates(list(reversed(files)))
        self.assertEqual(
            [(c.root_path, c.file_count) for c in first],
            [(c.root_path, c.file_count) for c in second],
        )
        self.assertEqual([c.root_path for c in first], sorted(c.root_path for c in first))


class TestAssignRootPath(unittest.TestCase):
    def test_longest_prefix_wins(self) -> None:
        roots = ["agent", "agent/services"]
        self.assertEqual(assign_root_path("agent/services/x.py", roots), "agent/services")
        self.assertEqual(assign_root_path("agent/cli.py", roots), "agent")
        self.assertIsNone(assign_root_path("other/file.py", roots))


class TestPackageSignals(unittest.TestCase):
    def test_collect_package_prefixes_uses_package_and_namespace(self) -> None:
        records = [
            {"id": "1", "package": "com.example.billing.api"},
            {"id": "2", "package": "com.example.billing.core"},
            {"id": "3", "namespace": "Example.Billing"},
            {"id": "4"},
        ]
        counts = collect_package_prefixes(records)
        self.assertEqual(counts["com.example"], 2)
        self.assertEqual(counts["Example.Billing"], 1)

    def test_package_prefixes_from_manifest(self) -> None:
        manifest = {
            "package_type_index": {
                "com.example.billing": ["Invoice", "Payment"],
                "com.example.identity": ["User"],
            }
        }
        counts = package_prefixes_from_manifest(manifest)
        self.assertEqual(counts["com.example"], 3)
        self.assertEqual(package_prefixes_from_manifest({}), {})


if __name__ == "__main__":
    unittest.main()
